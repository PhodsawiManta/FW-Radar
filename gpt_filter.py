import json
import re
from typing import Any, Dict, List
from openai import AsyncOpenAI
from config import OPENAI_API_KEY, OPENAI_BASE_URL, BotSettings


SYSTEM_PROMPT = """คุณคือผู้ช่วยคัดกรองงานฟรีแลนซ์อัจฉริยะ (Fastwork Job Filter AI)
หน้าที่ของคุณคือวิเคราะห์รายการประกาศงานจาก Fastwork Jobboard แล้วคัดเลือกเฉพาะงานที่ตรงกับความต้องการของผู้ใช้

ให้ตอบกลับเป็น JSON Object รูปแบบดังนี้เท่านั้น:
{
  "results": [
    {
      "id": "<รหัสงาน>",
      "matched": true หรือ false,
      "score": <คะแนนความตรงใจ 0-100>,
      "reason": "<สรุปสั้นๆ 1-2 ประโยคว่าทำไมงานนี้ถึงตรงหรือไม่ตรง และมีรายละเอียดสำคัญอะไรบ้าง>",
      "suggested_pitch": "<หาก matched=true ให้ร่างข้อความสั้นๆ ที่เป็นมิตรและเป็นมืออาชีพ 2-3 ประโยคสำหรับทักไปเสนอรับงานนี้บน Fastwork หาก matched=false ให้ใส่ค่าว่าง \"\">"
    }
  ]
}"""


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    return json.loads(text)


async def _evaluate_batch(
    client: AsyncOpenAI,
    batch_jobs: List[Dict[str, Any]],
    settings: BotSettings,
) -> Dict[str, Dict[str, Any]]:
    compact_jobs = [
        {
            "id": j["id"],
            "title": j.get("display_title") or j.get("title"),
            "category": j.get("category"),
            "type": j.get("type_label"),
            "budget": j.get("budget_display"),
            "description": (j.get("description") or "")[:600],
        }
        for j in batch_jobs
    ]

    user_prompt = (
        f"เกณฑ์งานที่ผู้ใช้งานต้องการ (Filter Criteria):\n{settings.filter_criteria}\n\n"
        f"เกณฑ์คะแนนขั้นต่ำ (Min Match Score): {settings.min_match_score}/100\n\n"
        f"รายการประกาศงานจาก Fastwork ({len(compact_jobs)} งาน):\n"
        f"{json.dumps(compact_jobs, ensure_ascii=False, indent=2)}"
    )

    response = await client.chat.completions.create(
        model=settings.gpt_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=1,
    )

    content = response.choices[0].message.content or "{}"
    parsed = _extract_json(content)
    results_list = parsed.get("results") or []

    evaluations: Dict[str, Dict[str, Any]] = {}
    for item in results_list:
        jid = str(item.get("id", ""))
        score = int(item.get("score") or 0)
        matched = bool(item.get("matched")) and score >= settings.min_match_score
        evaluations[jid] = {
            "matched": matched,
            "score": score,
            "reason": str(item.get("reason") or ""),
            "suggested_pitch": str(item.get("suggested_pitch") or ""),
        }
    return evaluations


async def evaluate_jobs_with_luna(
    jobs: List[Dict[str, Any]],
    settings: BotSettings,
    batch_size: int = 15,
) -> Dict[str, Dict[str, Any]]:
    """
    Evaluates Fastwork jobs across one or more pages using GPT Luna (`gpt-6-luna` by default),
    chunking into `batch_size` per request so multi-page scans stay fast and reliable.
    """
    if not jobs:
        return {}

    if not OPENAI_API_KEY or OPENAI_API_KEY == "your_openai_api_key_here":
        raise RuntimeError(
            "ยังไม่ได้ตั้งค่า OPENAI_API_KEY ในไฟล์ .env กรุณาใส่ API Key ก่อนใช้งานระบบคัดกรองด้วย GPT Luna"
        )

    client = AsyncOpenAI(
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL or "https://api.openai.com/v1",
    )

    all_evaluations: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(jobs), batch_size):
        batch = jobs[i : i + batch_size]
        batch_eval = await _evaluate_batch(client, batch, settings)
        all_evaluations.update(batch_eval)

    return all_evaluations
