import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = DATA_DIR / "settings.json"
DB_FILE = DATA_DIR / "jobs.db"

DEFAULT_CRITERIA = (
    "สนใจงานประเภท: เขียนโปรแกรม (Programming), พัฒนาเว็บไซต์ (Web Development, Frontend, Backend, "
    "Full-stack, WordPress, Landing Page), ทำแอปพลิเคชัน (Mobile App, iOS, Android, Flutter, React Native), "
    "ทำบอท (Bot, LINE OA, Telegram, Discord, Automation, Web Scraping), เชื่อมต่อระบบ API, "
    "AI / Machine Learning / LLM / GPT, Database, แก้บั๊ก, หรือที่ปรึกษาทำแอป/ซอฟต์แวร์/IT "
    "ไม่สนใจงานประเภท: แม่บ้าน, ขับรถ, พิธีกร/VJ/MC ไลฟ์สด, ถ่ายภาพ, แต่งหน้า, หรือพนักงานขายทั่วไป"
)


@dataclass
class BotSettings:
    gpt_model: str = os.getenv("GPT_MODEL", "gpt-6-luna")
    poll_interval_seconds: int = int(os.getenv("POLL_INTERVAL_SECONDS", "120"))
    page_size: int = int(os.getenv("FASTWORK_PAGE_SIZE", "20"))
    max_pages: int = int(os.getenv("FASTWORK_MAX_PAGES", "10"))
    target_date: str = os.getenv("FASTWORK_TARGET_DATE", "today")
    min_match_score: int = int(os.getenv("MIN_MATCH_SCORE", "65"))
    filter_criteria: str = os.getenv("JOB_FILTER_CRITERIA", DEFAULT_CRITERIA)
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    is_paused: bool = False

    @classmethod
    def load(cls) -> "BotSettings":
        settings = cls()
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                for key, value in data.items():
                    if hasattr(settings, key) and value not in (None, ""):
                        setattr(settings, key, value)
            except Exception:
                pass
        # Always allow .env GPT_MODEL to take precedence if set explicitly
        env_model = os.getenv("GPT_MODEL", "").strip()
        if env_model:
            settings.gpt_model = env_model
        return settings

    def save(self) -> None:
        SETTINGS_FILE.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
