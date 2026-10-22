import asyncio
import html
from typing import Any, Callable, Coroutine, Dict, Optional
import httpx
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, BotSettings
from fastwork_client import format_target_dates_summary, parse_target_dates
from storage import JobStorage


def format_job_alert_html(job: Dict[str, Any], eval_result: Dict[str, Any]) -> str:
    title = html.escape(job.get("display_title") or job.get("title") or "ไม่ระบุชื่องาน")
    category = html.escape(job.get("category") or "-")
    type_label = html.escape(job.get("type_label") or "-")
    budget = html.escape(job.get("budget_display") or "฿ ไม่ระบุ")
    posted = html.escape(job.get("inserted_at") or "-")
    deadline = html.escape(job.get("deadline_at") or "-")
    poster = html.escape(job.get("poster_name") or "-")
    offers_count = job.get("offers_count", 0)
    page_num = job.get("page", 1)
    score = eval_result.get("score", 0)
    reason = html.escape(eval_result.get("reason") or "-")
    pitch = html.escape(eval_result.get("suggested_pitch") or "")

    desc_raw = (job.get("description") or "").strip()
    if len(desc_raw) > 350:
        desc_raw = desc_raw[:350] + "..."
    desc = html.escape(desc_raw) if desc_raw else "<i>(ไม่มีรายละเอียดเพิ่มเติม)</i>"

    lines = [
        f"🎯 <b>พบงานใหม่ที่ตรงใจบน Fastwork!</b> (คะแนน {score}/100)",
        f"📌 <b>{title}</b>",
        "",
        f"🏷 <b>หมวดหมู่:</b> {category}",
        f"💼 <b>ลักษณะการจ้าง:</b> {type_label}",
        f"💰 <b>งบประมาณ:</b> <code>{budget}</code>",
        f"👤 <b>ผู้ว่าจ้าง:</b> {poster} (สมัครแล้ว {offers_count} คน)",
        f"🕒 <b>ลงประกาศเมื่อ:</b> {posted} (หน้า {page_num}) | <b>ส่งมอบ:</b> {deadline}",
        "",
        f"📝 <b>รายละเอียดงาน:</b>\n{desc}",
        "",
        f"🤖 <b>วิเคราะห์โดย GPT Luna:</b>\n{reason}",
    ]

    if pitch:
        lines.extend(
            [
                "",
                f"💬 <b>ข้อความแนะนำสำหรับทักรับงาน:</b>\n<code>{pitch}</code>",
            ]
        )

    return "\n".join(lines)


class TelegramService:
    def __init__(
        self,
        settings: BotSettings,
        storage: JobStorage,
        trigger_scan_callback: Callable[[Optional[str]], Coroutine[Any, Any, Dict[str, Any]]],
    ) -> None:
        self.token = TELEGRAM_BOT_TOKEN
        self.default_chat_id = settings.telegram_chat_id or TELEGRAM_CHAT_ID
        self.settings = settings
        self.storage = storage
        self.trigger_scan_callback = trigger_scan_callback
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self._offset: int = 0

    @property
    def is_configured(self) -> bool:
        return bool(
            self.token
            and self.token != "your_telegram_bot_token_here"
        )

    async def send_message(
        self,
        text: str,
        chat_id: Optional[str] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> bool:
        target_chat = chat_id or self.default_chat_id
        if not self.is_configured or not target_chat or target_chat == "your_telegram_chat_id_here":
            print(f"[Telegram] (Waiting for Chat ID — send /start in your chat or channel)\n{text}\n")
            return False

        payload: Dict[str, Any] = {
            "chat_id": target_chat,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.post(f"{self.base_url}/sendMessage", json=payload)
                # Auto-fix Channel ID if user entered positive ID or missed '-' / '-100' prefix
                if resp.status_code == 400 and "chat not found" in resp.text.lower():
                    raw = str(target_chat).lstrip("-")
                    if raw.isdigit():
                        alt_chat = f"-{raw}" if raw.startswith("100") else f"-100{raw}"
                        payload["chat_id"] = alt_chat
                        resp = await client.post(f"{self.base_url}/sendMessage", json=payload)
                        if resp.status_code == 200:
                            self.default_chat_id = alt_chat
                            self.settings.telegram_chat_id = alt_chat
                            self.settings.save()
                            print(f"[Telegram] Auto-corrected Channel ID to {alt_chat}")
                if resp.status_code != 200:
                    print(f"[Telegram] API Error ({resp.status_code}) for chat_id={payload['chat_id']}: {resp.text}")
                resp.raise_for_status()
                return True
            except Exception as err:
                print(f"[Telegram] Error sending message to {target_chat}: {err}")
                return False

    async def send_job_alert(
        self, job: Dict[str, Any], eval_result: Dict[str, Any], chat_id: Optional[str] = None
    ) -> bool:
        text = format_job_alert_html(job, eval_result)
        job_url = job.get("url") or "https://jobboard.fastwork.co/jobs"
        reply_markup = {
            "inline_keyboard": [
                [{"text": "🔗 ดูรายละเอียด & สมัครงานบน Fastwork", "url": job_url}]
            ]
        }
        return await self.send_message(text=text, chat_id=chat_id, reply_markup=reply_markup)

    async def poll_commands_forever(self) -> None:
        if not self.is_configured:
            print("[Telegram] Bot token not set. Skipping Telegram command polling.")
            return

        async with httpx.AsyncClient(timeout=40.0) as client:
            try:
                me_resp = await client.get(f"{self.base_url}/getMe")
                if me_resp.status_code == 200:
                    bot_info = me_resp.json().get("result", {})
                    print(
                        f"[Telegram] Connected as @{bot_info.get('username')} "
                        f"({bot_info.get('first_name')}) | Default Chat ID: {self.default_chat_id or 'Auto-detect'}"
                    )
                else:
                    print(f"[Telegram] Invalid TELEGRAM_BOT_TOKEN! Response: {me_resp.text}")
                    return

                # Ensure no webhook is blocking getUpdates
                await client.post(
                    f"{self.base_url}/deleteWebhook",
                    json={"drop_pending_updates": False},
                )
            except Exception as init_err:
                print(f"[Telegram] Initialization warning: {init_err}")

            print("[Telegram] Listening for commands in Private Chat, Groups, and Channels...")
            while True:
                try:
                    resp = await client.get(
                        f"{self.base_url}/getUpdates",
                        params={
                            "offset": self._offset,
                            "timeout": 25,
                            "allowed_updates": '["message","channel_post","edited_message","edited_channel_post"]',
                        },
                    )
                    resp.raise_for_status()
                    updates = resp.json().get("result") or []
                    for upd in updates:
                        self._offset = int(upd["update_id"]) + 1
                        asyncio.create_task(self._handle_update(upd))
                except asyncio.CancelledError:
                    break
                except Exception as err:
                    print(f"[Telegram] Polling warning: {err}")
                    await asyncio.sleep(5)

    async def _handle_update(self, update: Dict[str, Any]) -> None:
        msg = (
            update.get("message")
            or update.get("channel_post")
            or update.get("edited_message")
            or update.get("edited_channel_post")
            or {}
        )
        text = str(msg.get("text") or msg.get("caption") or "").strip()
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id") or "")
        chat_title = chat.get("title") or chat.get("username") or chat.get("first_name") or chat_id
        if not text or not chat_id:
            return

        print(f"[Telegram] Received message from '{chat_title}' (ID: {chat_id}): {text}")

        # Auto-save numeric chat_id (especially for Channels like -100xxxxxxxxxx)
        if (
            not self.default_chat_id
            or self.default_chat_id == "your_telegram_chat_id_here"
            or not (self.default_chat_id.lstrip("-").isdigit() or self.default_chat_id.startswith("@"))
        ):
            self.default_chat_id = chat_id
            self.settings.telegram_chat_id = chat_id
            self.settings.save()
            print(f"[Telegram] Saved active Chat/Channel ID: {chat_id}")

        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/start", "/help"):
            help_msg = (
                "🤖 <b>Fastwork Jobboard AI Bot (GPT Luna)</b>\n\n"
                "คำสั่งที่ใช้งานได้:\n"
                "• /status — ดูสถานะบอท วันที่ตั้งค่า จำนวนหน้า และสถิติงาน\n"
                "• /check <code>[วันที่]</code> — สั่งไล่ดึงงานตามหน้าและวิเคราะห์ทันที (เช่น <code>/check</code> หรือ <code>/check 21/10/2026</code>)\n"
                "• /setdate <code>&lt;วันที่&gt;</code> — ตั้งค่าวันที่ต้องการดึงงาน (ค่าเริ่มต้น: <code>today</code>)\n"
                "  ตัวอย่าง: <code>/setdate today</code>, <code>/setdate yesterday</code>, <code>/setdate 3d</code>, <code>/setdate 22/10/2026</code>, <code>/setdate 20/10/2026-22/10/2026</code>\n"
                "• /setpages <code>&lt;จำนวนหน้า&gt;</code> — ตั้งจำนวนหน้าสูงสุดที่จะไล่ดึง (เช่น <code>/setpages 10</code>)\n"
                "• /recent — ดูรายการงานที่ตรงใจ 5 งานล่าสุด\n"
                "• /setfilter <code>&lt;เกณฑ์งาน&gt;</code> — เปลี่ยนเกณฑ์คัดกรองงานของ GPT Luna\n"
                "• /setmodel <code>&lt;ชื่อโมเดล&gt;</code> — เปลี่ยนโมเดล (เช่น <code>gpt-6-luna</code>)\n"
                "• /setscore <code>&lt;0-100&gt;</code> — ตั้งคะแนนความตรงใจขั้นต่ำ\n"
                "• /setinterval <code>&lt;วินาที&gt;</code> — ตั้งความถี่ในการเช็คงานใหม่\n"
                "• /pause / /resume — หยุดชั่วคราว / ทำงานต่อ\n\n"
                f"🆔 <b>Chat ID ของห้องนี้:</b> <code>{chat_id}</code>"
            )
            await self.send_message(help_msg, chat_id=chat_id)

        elif cmd == "/status":
            stats = self.storage.get_stats()
            state_str = "⏸ หยุดชั่วคราว (Paused)" if self.settings.is_paused else "🟢 กำลังทำงาน (Active)"
            date_summary = html.escape(format_target_dates_summary(self.settings.target_date))
            status_msg = (
                f"📊 <b>สถานะระบบ Fastwork Job Bot</b>\n\n"
                f"• <b>สถานะ:</b> {state_str}\n"
                f"• <b>โมเดล AI:</b> <code>{html.escape(self.settings.gpt_model)}</code>\n"
                f"• <b>วันที่ดึงงาน (Target Date):</b> <code>{date_summary}</code>\n"
                f"• <b>จำนวนหน้าสูงสุดที่ไล่ดึง:</b> {self.settings.max_pages} หน้า ({self.settings.page_size} งาน/หน้า)\n"
                f"• <b>คะแนนขั้นต่ำ:</b> {self.settings.min_match_score}/100\n"
                f"• <b>ความถี่การเช็ค:</b> ทุกๆ {self.settings.poll_interval_seconds} วินาที\n"
                f"• <b>ตรวจแล้วทั้งหมด:</b> {stats['total_seen']} งาน (ตรงใจ {stats['total_matched']} งาน)\n\n"
                f"🎯 <b>เกณฑ์การคัดกรองปัจจุบัน:</b>\n"
                f"<code>{html.escape(self.settings.filter_criteria)}</code>"
            )
            await self.send_message(status_msg, chat_id=chat_id)

        elif cmd == "/check":
            target_spec = arg if arg else self.settings.target_date
            try:
                parse_target_dates(target_spec)
            except ValueError as verr:
                await self.send_message(f"⚠️ {html.escape(str(verr))}", chat_id=chat_id)
                return

            date_label = html.escape(format_target_dates_summary(target_spec))
            await self.send_message(
                f"🔍 กำลังไล่ดึงงานจาก Fastwork (วันที่: <b>{date_label}</b>, สูงสุด {self.settings.max_pages} หน้า) "
                f"และวิเคราะห์ด้วย GPT Luna...",
                chat_id=chat_id,
            )
            summary = await self.trigger_scan_callback(arg if arg else None)
            await self.send_message(
                f"✅ <b>ตรวจสอบเสร็จสิ้น!</b>\n"
                f"• วันที่เป้าหมาย: <code>{html.escape(str(summary.get('date_summary', target_spec)))}</code>\n"
                f"• ไล่ตรวจไปทั้งหมด: {summary.get('pages_scanned', 1)} หน้า\n"
                f"• พบงานในวันที่กำหนด: {summary.get('fetched', 0)} งาน\n"
                f"• งานใหม่ที่เพิ่งวิเคราะห์: {summary.get('new_evaluated', 0)} งาน\n"
                f"• งานที่ตรงเกณฑ์: {summary.get('matched', 0)} งาน",
                chat_id=chat_id,
            )

        elif cmd == "/setdate":
            if not arg:
                await self.send_message(
                    "⚠️ กรุณาระบุวันที่ต้องการดึงงาน เช่น:\n"
                    "• <code>/setdate today</code> (วันนี้ — ค่าเริ่มต้น)\n"
                    "• <code>/setdate yesterday</code> (เมื่อวาน)\n"
                    "• <code>/setdate 3d</code> (ย้อนหลัง 3 วัน)\n"
                    "• <code>/setdate 22/10/2026</code> (ระบุวันที่)\n"
                    "• <code>/setdate 20/10/2026-22/10/2026</code> (ระบุช่วงวันที่)\n"
                    "• <code>/setdate all</code> (ทุกวันตามจำนวนหน้า)",
                    chat_id=chat_id,
                )
                return
            try:
                parse_target_dates(arg)
            except ValueError as verr:
                await self.send_message(f"⚠️ {html.escape(str(verr))}", chat_id=chat_id)
                return

            self.settings.target_date = arg
            self.settings.save()
            summary_str = html.escape(format_target_dates_summary(arg))
            await self.send_message(
                f"✅ ตั้งค่าวันที่ดึงงานเป็น: <b>{summary_str}</b> เรียบร้อยแล้ว",
                chat_id=chat_id,
            )

        elif cmd == "/setpages":
            try:
                val = max(1, min(100, int(arg)))
                self.settings.max_pages = val
                self.settings.save()
                await self.send_message(
                    f"✅ ตั้งจำนวนหน้าสูงสุดที่จะไล่ดึง (Pagination) เป็น <b>{val} หน้า</b> เรียบร้อยแล้ว",
                    chat_id=chat_id,
                )
            except ValueError:
                await self.send_message(
                    "⚠️ กรุณาระบุจำนวนหน้าเป็นตัวเลข (1-100) เช่น <code>/setpages 10</code>",
                    chat_id=chat_id,
                )

        elif cmd == "/recent":
            matches = self.storage.get_recent_matches(limit=5)
            if not matches:
                await self.send_message("📭 ยังไม่มีประวัติงานที่ตรงเกณฑ์ในฐานข้อมูล", chat_id=chat_id)
                return
            lines = ["📌 <b>5 งานล่าสุดที่ตรงเกณฑ์:</b>\n"]
            for idx, m in enumerate(matches, 1):
                title = html.escape(m.get("title") or "-")
                budget = html.escape(m.get("budget") or "-")
                score = m.get("score", 0)
                url = html.escape(m.get("url") or "https://jobboard.fastwork.co/jobs")
                lines.append(f"{idx}. <a href=\"{url}\">{title}</a> ({budget}) — <b>{score}/100</b>")
            await self.send_message("\n".join(lines), chat_id=chat_id)

        elif cmd == "/setfilter":
            if not arg:
                await self.send_message(
                    "⚠️ กรุณาระบุเกณฑ์งานหลังคำสั่ง เช่น:\n"
                    "<code>/setfilter สนใจงานทำเว็บไซต์ Python, React, บอท Telegram และ AI งบ 3,000 บาทขึ้นไป</code>",
                    chat_id=chat_id,
                )
                return
            self.settings.filter_criteria = arg
            self.settings.save()
            await self.send_message(
                f"✅ อัปเดตเกณฑ์คัดกรองงานสำหรับ GPT Luna เรียบร้อยแล้ว:\n<code>{html.escape(arg)}</code>",
                chat_id=chat_id,
            )

        elif cmd == "/setmodel":
            if not arg:
                await self.send_message(
                    "⚠️ กรุณาระบุชื่อโมเดล เช่น: <code>/setmodel gpt-6-luna</code> หรือ <code>/setmodel gpt-5.6-luna</code>",
                    chat_id=chat_id,
                )
                return
            self.settings.gpt_model = arg
            self.settings.save()
            await self.send_message(
                f"✅ เปลี่ยนโมเดล AI เป็น <code>{html.escape(arg)}</code> เรียบร้อยแล้ว",
                chat_id=chat_id,
            )

        elif cmd == "/setscore":
            try:
                val = max(0, min(100, int(arg)))
                self.settings.min_match_score = val
                self.settings.save()
                await self.send_message(f"✅ ตั้งคะแนนความตรงใจขั้นต่ำเป็น <b>{val}/100</b> เรียบร้อยแล้ว", chat_id=chat_id)
            except ValueError:
                await self.send_message("⚠️ กรุณาระบุตัวเลข 0-100 เช่น <code>/setscore 70</code>", chat_id=chat_id)

        elif cmd == "/setinterval":
            try:
                val = max(30, int(arg))
                self.settings.poll_interval_seconds = val
                self.settings.save()
                await self.send_message(f"✅ ตั้งความถี่ในการเช็คงานใหม่เป็นทุกๆ <b>{val} วินาที</b>", chat_id=chat_id)
            except ValueError:
                await self.send_message("⚠️ กรุณาระบุเวลาเป็นวินาที (ขั้นต่ำ 30) เช่น <code>/setinterval 120</code>", chat_id=chat_id)

        elif cmd == "/pause":
            self.settings.is_paused = True
            self.settings.save()
            await self.send_message("⏸ หยุดการเช็คงานอัตโนมัติชั่วคราวแล้ว (พิมพ์ /resume เพื่อทำงานต่อ)", chat_id=chat_id)

        elif cmd == "/resume":
            self.settings.is_paused = False
            self.settings.save()
            await self.send_message("▶️ เปิดการเช็คงานอัตโนมัติต่อแล้ว!", chat_id=chat_id)
