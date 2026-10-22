import argparse
import asyncio
import os
import sys
from typing import Any, Dict, Optional
from config import DATA_DIR, BotSettings
from fastwork_client import (
    fetch_jobs_by_pages_and_date,
    format_target_dates_summary,
)
from gpt_filter import evaluate_jobs_with_luna
from storage import JobStorage
from telegram_bot import TelegramService

PID_FILE = DATA_DIR / "bot.pid"


class FastworkMonitorApp:
    def __init__(self) -> None:
        self.settings = BotSettings.load()
        self.storage = JobStorage()
        self.telegram = TelegramService(
            settings=self.settings,
            storage=self.storage,
            trigger_scan_callback=self.run_single_scan,
        )
        self._scan_lock = asyncio.Lock()

    async def run_single_scan(
        self,
        override_date_spec: Optional[str] = None,
        override_max_pages: Optional[int] = None,
    ) -> Dict[str, Any]:
        async with self._scan_lock:
            date_spec = override_date_spec or self.settings.target_date or "today"
            max_pages = override_max_pages or self.settings.max_pages or 10
            date_summary = format_target_dates_summary(date_spec)

            print(
                f"[Scanner] Crawling Fastwork pages (max_pages={max_pages}, page_size={self.settings.page_size}, "
                f"target_date={date_summary})..."
            )
            jobs, pages_scanned = await fetch_jobs_by_pages_and_date(
                page_size=self.settings.page_size,
                max_pages=max_pages,
                target_date_spec=date_spec,
            )
            unseen_jobs = self.storage.filter_unseen(jobs)

            print(
                f"[Scanner] Scanned {pages_scanned} page(s) | "
                f"Matched date ({date_spec}): {len(jobs)} jobs | New unseen: {len(unseen_jobs)} jobs"
            )
            if not unseen_jobs:
                return {
                    "pages_scanned": pages_scanned,
                    "date_summary": date_summary,
                    "fetched": len(jobs),
                    "new_evaluated": 0,
                    "matched": 0,
                }

            try:
                evaluations = await evaluate_jobs_with_luna(unseen_jobs, self.settings)
            except Exception as err:
                print(f"[GPT Luna] Evaluation error: {err}")
                return {
                    "pages_scanned": pages_scanned,
                    "date_summary": date_summary,
                    "fetched": len(jobs),
                    "new_evaluated": 0,
                    "matched": 0,
                }

            matched_count = 0
            # Process oldest-to-newest so notifications arrive in chronological order
            for job in reversed(unseen_jobs):
                eval_res = evaluations.get(
                    job["id"],
                    {"matched": False, "score": 0, "reason": "No evaluation returned", "suggested_pitch": ""},
                )
                is_matched = bool(eval_res.get("matched"))
                score = int(eval_res.get("score") or 0)
                reason = str(eval_res.get("reason") or "")
                pitch = str(eval_res.get("suggested_pitch") or "")

                self.storage.save_job_result(
                    job=job,
                    matched=is_matched,
                    score=score,
                    reason=reason,
                    suggested_pitch=pitch,
                )

                status_tag = "MATCH" if is_matched else "SKIP"
                print(
                    f"  [{status_tag}] ({score}/100) [Page {job.get('page', 1)} | {job.get('inserted_at')}] "
                    f"{job.get('display_title')} | {job.get('budget_display')}"
                )

                if is_matched:
                    matched_count += 1
                    await self.telegram.send_job_alert(job, eval_res)

            return {
                "pages_scanned": pages_scanned,
                "date_summary": date_summary,
                "fetched": len(jobs),
                "new_evaluated": len(unseen_jobs),
                "matched": matched_count,
            }

    async def poll_jobs_forever(self) -> None:
        print(
            f"[FastworkBot] Started background monitor | Model: {self.settings.gpt_model} | "
            f"Target Date: {format_target_dates_summary(self.settings.target_date)} | "
            f"Max Pages: {self.settings.max_pages} | Interval: {self.settings.poll_interval_seconds}s"
        )
        while True:
            try:
                if not self.settings.is_paused:
                    await self.run_single_scan()
            except asyncio.CancelledError:
                break
            except Exception as err:
                print(f"[FastworkBot] Loop error: {err}")

            await asyncio.sleep(max(30, self.settings.poll_interval_seconds))

    async def start(self) -> None:
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        try:
            await asyncio.gather(
                self.poll_jobs_forever(),
                self.telegram.poll_commands_forever(),
            )
        finally:
            if PID_FILE.exists():
                try:
                    PID_FILE.unlink()
                except Exception:
                    pass


async def test_fetch_only(date_spec: str, max_pages: int, page_size: int) -> None:
    """Fetches live jobs across pages from Fastwork for the specified date(s) and prints them."""
    summary_str = format_target_dates_summary(date_spec)
    print(f"Crawling Fastwork Jobboard | Target Date: {summary_str} | Max Pages: {max_pages}...")
    jobs, pages_scanned = await fetch_jobs_by_pages_and_date(
        page_size=page_size,
        max_pages=max_pages,
        target_date_spec=date_spec,
    )
    print(
        f"\nScanned {pages_scanned} page(s) -> Found {len(jobs)} job(s) matching '{summary_str}':\n"
        + "=" * 80
    )
    for idx, j in enumerate(jobs, 1):
        print(
            f"{idx:2d}. [หน้า {j.get('page', 1)}] {j['display_title']}\n"
            f"    หมวดหมู่: {j['category']} | ประเภท: {j['type_label']} | งบ: {j['budget_display']} | เมื่อ: {j['inserted_at']}\n"
            f"    ลิงก์: {j['url']}"
        )


def main() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Fastwork Jobboard Bot with Multi-Page & Date Filter, GPT Luna & Telegram Alert")
    parser.add_argument(
        "--test-fetch",
        action="store_true",
        help="Test fetching live jobs across pages from Fastwork without calling OpenAI/Telegram",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single multi-page scan + GPT Luna filter + Telegram alert and exit",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Target date(s) to fetch: 'today' (default), 'yesterday', '3d', '22/10/2026', '2026-10-22', '20/10/2026-22/10/2026', or 'all'",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Maximum number of pages (?page=1, 2, ...) to crawl (default: from .env / settings, 10)",
    )
    args = parser.parse_args()

    settings = BotSettings.load()
    if args.date:
        settings.target_date = args.date
    if args.max_pages:
        settings.max_pages = args.max_pages

    if args.test_fetch:
        asyncio.run(
            test_fetch_only(
                date_spec=settings.target_date,
                max_pages=settings.max_pages,
                page_size=settings.page_size,
            )
        )
    elif args.once:
        app = FastworkMonitorApp()
        asyncio.run(
            app.run_single_scan(
                override_date_spec=args.date,
                override_max_pages=args.max_pages,
            )
        )
    else:
        app = FastworkMonitorApp()
        if args.date:
            app.settings.target_date = args.date
        if args.max_pages:
            app.settings.max_pages = args.max_pages
        try:
            asyncio.run(app.start())
        except KeyboardInterrupt:
            print("\n[FastworkBot] Shutting down...")


if __name__ == "__main__":
    main()
