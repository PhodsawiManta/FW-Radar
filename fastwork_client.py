import hashlib
import re
from datetime import date, datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
import httpx
from bs4 import BeautifulSoup

FASTWORK_API_URL = "https://jobboard-api.fastwork.co/api/jobs"
FASTWORK_WEB_URL = "https://jobboard.fastwork.co/jobs"

JOB_TYPE_MAP = {
    "freelance": "ฟรีแลนซ์",
    "full-time": "งานประจำ",
    "part-time": "พาร์ทไทม์",
    "contract": "สัญญาจ้าง",
}

TH_TZ = timezone(timedelta(hours=7))


def _format_budget(raw_budget: Any) -> str:
    if raw_budget is None or str(raw_budget).strip() == "":
        return "฿ ไม่ระบุ"
    text = str(raw_budget).strip()
    if text.startswith("฿"):
        return text
    try:
        val = int(float(text))
        if val <= 0:
            return "฿ ไม่ระบุ"
        return f"฿{val:,}"
    except ValueError:
        return f"฿{text}"


def _parse_iso_to_th_datetime(iso_str: Any) -> Optional[datetime]:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return dt.astimezone(TH_TZ)
    except Exception:
        return None


def _format_iso_time(iso_str: Any) -> str:
    dt_th = _parse_iso_to_th_datetime(iso_str)
    if dt_th:
        return dt_th.strftime("%d/%m/%Y %H:%M")
    return str(iso_str) if iso_str else "-"


def _format_iso_date(iso_str: Any) -> str:
    dt_th = _parse_iso_to_th_datetime(iso_str)
    if dt_th:
        return dt_th.strftime("%d/%m/%Y")
    return str(iso_str) if iso_str else "-"


def _parse_single_date_token(token: str, today: date) -> date:
    t = token.strip().lower()
    if t in ("today", "now", "now day", "nowday", "วันนี้", ""):
        return today
    if t in ("yesterday", "เมื่อวาน"):
        return today - timedelta(days=1)

    # Format: DD/MM/YYYY or DD-MM-YYYY
    m_dmy = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", t)
    if m_dmy:
        day_i, month_i, year_i = int(m_dmy.group(1)), int(m_dmy.group(2)), int(m_dmy.group(3))
        if year_i >= 2500:
            year_i -= 543
        return date(year_i, month_i, day_i)

    # Format: YYYY-MM-DD or YYYY/MM/DD
    m_ymd = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$", t)
    if m_ymd:
        year_i, month_i, day_i = int(m_ymd.group(1)), int(m_ymd.group(2)), int(m_ymd.group(3))
        if year_i >= 2500:
            year_i -= 543
        return date(year_i, month_i, day_i)

    raise ValueError(
        f"รูปแบบวันที่ไม่ถูกต้อง: '{token}' (ใช้ได้เช่น today, yesterday, 3d, 22/10/2026, 2026-10-22, หรือ 20/10/2026-22/10/2026)"
    )


def parse_target_dates(date_spec: str) -> Optional[Set[date]]:
    """
    Parses a user-provided date specification into a set of target `datetime.date` objects (in UTC+7).
    Returns `None` if the user wants all dates (`all` / `*`).
    Supported examples:
      - "today", "now", "now day", "วันนี้" (default)
      - "yesterday", "เมื่อวาน"
      - "3d", "7d", "3วัน" (last N days including today)
      - "22/10/2026" or "2026-10-22"
      - "20/10/2026-22/10/2026" or "2026-10-20..2026-10-22" or "2026-10-20 to 2026-10-22"
      - "20/10/2026, 22/10/2026"
      - "all", "*"
    """
    spec = (date_spec or "today").strip()
    if spec.lower() in ("all", "*", "ทั้งหมด"):
        return None

    today = datetime.now(TH_TZ).date()

    # Check "Nd" / "N days" / "Nวัน"
    m_days = re.match(r"^(\d+)\s*(?:d|days?|วัน)$", spec.lower())
    if m_days:
        n_days = max(1, min(365, int(m_days.group(1))))
        return {today - timedelta(days=i) for i in range(n_days)}

    result_dates: Set[date] = set()
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        # Check range with "..", " to ", or " - " / "DD/MM/YYYY-DD/MM/YYYY"
        range_tokens: Optional[Tuple[str, str]] = None
        if ".." in part:
            a, b = part.split("..", 1)
            range_tokens = (a, b)
        elif " to " in part.lower():
            idx = part.lower().find(" to ")
            range_tokens = (part[:idx], part[idx + 4 :])
        else:
            m_slash_range = re.match(
                r"^(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})$", part
            )
            if m_slash_range:
                range_tokens = (m_slash_range.group(1), m_slash_range.group(2))
            else:
                m_iso_range = re.match(
                    r"^(\d{4}-\d{1,2}-\d{1,2})\s*(?:to|\.\.|~)\s*(\d{4}-\d{1,2}-\d{1,2})$",
                    part,
                )
                if m_iso_range:
                    range_tokens = (m_iso_range.group(1), m_iso_range.group(2))

        if range_tokens:
            d1 = _parse_single_date_token(range_tokens[0], today)
            d2 = _parse_single_date_token(range_tokens[1], today)
            start_d, end_d = (d1, d2) if d1 <= d2 else (d2, d1)
            curr = start_d
            while curr <= end_d:
                result_dates.add(curr)
                curr += timedelta(days=1)
        else:
            result_dates.add(_parse_single_date_token(part, today))

    return result_dates or {today}


def format_target_dates_summary(date_spec: str) -> str:
    try:
        dates = parse_target_dates(date_spec)
        if dates is None:
            return "ทุกวัน (All dates)"
        sorted_d = sorted(dates)
        if len(sorted_d) == 1:
            return f"{date_spec} ({sorted_d[0].strftime('%d/%m/%Y')})"
        return (
            f"{date_spec} ({sorted_d[0].strftime('%d/%m/%Y')} ถึง "
            f"{sorted_d[-1].strftime('%d/%m/%Y')}, รวม {len(sorted_d)} วัน)"
        )
    except Exception:
        return str(date_spec)


def normalize_api_job(raw: Dict[str, Any], page: int = 1) -> Dict[str, Any]:
    job_id = str(raw.get("id", "")).strip()
    title = str(raw.get("title", "")).strip()
    if not title.startswith("ฉันหา") and raw.get("kind") == "standard":
        display_title = f"ฉันหา{title}"
    else:
        display_title = title

    tag = raw.get("tag") or {}
    category = tag.get("name") or "ไม่ระบุหมวดหมู่"
    raw_type = str(raw.get("type") or "")
    type_label = JOB_TYPE_MAP.get(raw_type, raw_type or "ไม่ระบุ")
    budget_raw = raw.get("budget") or raw.get("budget_2")
    budget_display = _format_budget(budget_raw)

    user_profile = raw.get("user_profile") or {}
    poster_name = (
        raw.get("company_display_name")
        or user_profile.get("display_name")
        or user_profile.get("username")
        or "ไม่ระบุผู้ว่าจ้าง"
    )

    dt_th = _parse_iso_to_th_datetime(raw.get("inserted_at"))
    posted_date_iso = dt_th.date().isoformat() if dt_th else None

    return {
        "id": job_id,
        "title": title,
        "display_title": display_title,
        "description": str(raw.get("description") or "").strip(),
        "category": category,
        "type": raw_type,
        "type_label": type_label,
        "budget": budget_raw,
        "budget_display": budget_display,
        "inserted_at": _format_iso_time(raw.get("inserted_at")),
        "posted_date": posted_date_iso,
        "deadline_at": _format_iso_date(raw.get("deadline_at")),
        "offers_count": int(raw.get("freelance_offers_count") or 0),
        "require_english": bool(raw.get("require_english_speaker")),
        "poster_name": poster_name,
        "page": page,
        "url": f"{FASTWORK_WEB_URL}/{job_id}" if job_id else f"{FASTWORK_WEB_URL}?page={page}",
    }


def _parse_html_date_str(text: str) -> Optional[str]:
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text or "")
    if not m:
        return None
    try:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y >= 2500:
            y -= 543
        return date(y, mo, d).isoformat()
    except Exception:
        return None


def parse_jobs_from_html(html: str, page: int = 1) -> List[Dict[str, Any]]:
    """
    Fallback parser that extracts jobs directly from the Fastwork Jobboard HTML
    (supports both Desktop Table rows and Mobile Job Cards).
    """
    soup = BeautifulSoup(html, "html.parser")
    jobs: List[Dict[str, Any]] = []
    seen_titles = set()

    # 1. Parse Desktop Table (`table.trb-table tbody tr`)
    for tr in soup.select("table.trb-table tbody tr"):
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        title = tds[0].get_text(strip=True)
        category = tds[1].get_text(strip=True)
        type_label = tds[2].get_text(strip=True)
        budget_display = tds[3].get_text(strip=True)
        inserted_at = tds[4].get_text(strip=True)
        deadline_at = tds[5].get_text(strip=True)

        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        clean_title = title[5:].strip() if title.startswith("ฉันหา") else title
        synthetic_id = hashlib.md5(f"{clean_title}|{inserted_at}".encode("utf-8")).hexdigest()[:16]

        jobs.append(
            {
                "id": synthetic_id,
                "title": clean_title,
                "display_title": title,
                "description": "",
                "category": category or "ไม่ระบุ",
                "type": type_label,
                "type_label": type_label,
                "budget": budget_display,
                "budget_display": budget_display,
                "inserted_at": inserted_at,
                "posted_date": _parse_html_date_str(inserted_at),
                "deadline_at": deadline_at,
                "offers_count": 0,
                "require_english": False,
                "poster_name": "ไม่ระบุ",
                "page": page,
                "url": f"{FASTWORK_WEB_URL}?page={page}",
            }
        )

    # 2. Parse Mobile Cards (`div[data-selenium="job-listing.card.job"]`) if table wasn't matched
    if not jobs:
        for card in soup.select('[data-selenium="job-listing.card.job"]'):
            title_el = card.select_one('[class*="JobCardList_job-title"]')
            chip_el = card.select_one('[class*="JobCardList_chip__"]')
            price_el = card.select_one('[class*="JobCardList_price"]')
            applied_el = card.select_one('[class*="JobCardList_applied"]')

            title = title_el.get_text(strip=True) if title_el else ""
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            clean_title = title[5:].strip() if title.startswith("ฉันหา") else title
            type_label = chip_el.get_text(strip=True) if chip_el else "ไม่ระบุ"
            budget_display = price_el.get_text(strip=True) if price_el else "฿ ไม่ระบุ"
            applied_text = applied_el.get_text(strip=True) if applied_el else ""
            synthetic_id = hashlib.md5(f"{clean_title}|{budget_display}".encode("utf-8")).hexdigest()[:16]

            jobs.append(
                {
                    "id": synthetic_id,
                    "title": clean_title,
                    "display_title": title,
                    "description": applied_text,
                    "category": "ไม่ระบุ",
                    "type": type_label,
                    "type_label": type_label,
                    "budget": budget_display,
                    "budget_display": budget_display,
                    "inserted_at": "-",
                    "posted_date": datetime.now(TH_TZ).date().isoformat(),
                    "deadline_at": "-",
                    "offers_count": 0,
                    "require_english": False,
                    "poster_name": "ไม่ระบุ",
                    "page": page,
                    "url": f"{FASTWORK_WEB_URL}?page={page}",
                }
            )

    return jobs


async def fetch_single_page(
    client: httpx.AsyncClient, page: int = 1, page_size: int = 20
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Fetches a single page from Fastwork Jobboard API (`?page=X`),
    returning `(jobs_on_page, total_pages)`.
    Falls back to HTML scraping of `https://jobboard.fastwork.co/jobs?page=X` if needed.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://jobboard.fastwork.co",
        "Referer": f"https://jobboard.fastwork.co/jobs?page={page}",
    }
    params = [
        ("page", str(page)),
        ("page_size", str(page_size)),
        ("order_by[]", "inserted_at"),
        ("order_directions[]", "desc"),
        ("kind", "standard"),
    ]

    try:
        resp = await client.get(FASTWORK_API_URL, params=params, headers=headers)
        resp.raise_for_status()
        payload = resp.json()
        raw_jobs = payload.get("data") or []
        meta = payload.get("meta") or {}
        total_pages = int(meta.get("total_pages") or 1)
        if raw_jobs:
            return [normalize_api_job(item, page=page) for item in raw_jobs], total_pages
    except Exception as api_err:
        print(f"[FastworkClient] Page {page} API warning, falling back to HTML: {api_err}")

    # Fallback: scrape HTML page `?page=X`
    resp = await client.get(FASTWORK_WEB_URL, params={"page": str(page)}, headers=headers)
    resp.raise_for_status()
    return parse_jobs_from_html(resp.text, page=page), 999


async def fetch_jobs_by_pages_and_date(
    page_size: int = 20,
    max_pages: int = 10,
    target_date_spec: str = "today",
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Crawls through pages `page=1, 2, ...` up to `max_pages` on Fastwork Jobboard,
    filtering jobs that match `target_date_spec` (default: "today" in Thailand timezone UTC+7).
    Automatically stops crawling further pages once jobs on the page are older than the earliest target date.
    Returns `(matched_date_jobs, pages_scanned)`.
    """
    target_dates = parse_target_dates(target_date_spec)
    min_target_date: Optional[date] = min(target_dates) if target_dates else None

    collected_jobs: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    pages_scanned = 0

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for page in range(1, max(1, max_pages) + 1):
            page_jobs, total_pages = await fetch_single_page(
                client=client, page=page, page_size=page_size
            )
            pages_scanned += 1
            if not page_jobs:
                break

            older_than_min_count = 0
            dated_jobs_on_page = 0

            for job in page_jobs:
                jid = job["id"]
                if jid in seen_ids:
                    continue

                if target_dates is None:
                    seen_ids.add(jid)
                    collected_jobs.append(job)
                    continue

                posted_iso = job.get("posted_date")
                if not posted_iso:
                    # Include if date is unknown
                    seen_ids.add(jid)
                    collected_jobs.append(job)
                    continue

                dated_jobs_on_page += 1
                job_date = date.fromisoformat(posted_iso)

                if job_date in target_dates:
                    seen_ids.add(jid)
                    collected_jobs.append(job)
                elif min_target_date and job_date < min_target_date:
                    older_than_min_count += 1

            # Since Fastwork sorts jobs by inserted_at DESC, if the oldest jobs on this page
            # are already older than min_target_date, we don't need to crawl older pages!
            if min_target_date and dated_jobs_on_page > 0:
                last_job_iso = page_jobs[-1].get("posted_date")
                last_job_older = (
                    date.fromisoformat(last_job_iso) < min_target_date
                    if last_job_iso
                    else False
                )
                if older_than_min_count == dated_jobs_on_page or (
                    last_job_older and older_than_min_count >= 3
                ):
                    break

            if page >= total_pages:
                break

    return collected_jobs, pages_scanned


async def fetch_latest_jobs(page_size: int = 20, page: int = 1) -> List[Dict[str, Any]]:
    jobs, _ = await fetch_jobs_by_pages_and_date(
        page_size=page_size, max_pages=page, target_date_spec="today"
    )
    return jobs
