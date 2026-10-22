import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from config import DB_FILE


class JobStorage:
    def __init__(self, db_path: Path = DB_FILE) -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_jobs (
                    job_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    category TEXT,
                    job_type TEXT,
                    budget TEXT,
                    url TEXT,
                    matched INTEGER DEFAULT 0,
                    score INTEGER DEFAULT 0,
                    reason TEXT,
                    suggested_pitch TEXT,
                    inserted_at TEXT,
                    processed_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def is_seen(self, job_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            return row is not None

    def filter_unseen(self, jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not jobs:
            return []
        with self._connect() as conn:
            placeholders = ",".join("?" for _ in jobs)
            ids = [j["id"] for j in jobs]
            rows = conn.execute(
                f"SELECT job_id FROM seen_jobs WHERE job_id IN ({placeholders})",
                ids,
            ).fetchall()
            seen_ids = {r["job_id"] for r in rows}
        return [j for j in jobs if j["id"] not in seen_ids]

    def save_job_result(
        self,
        job: Dict[str, Any],
        matched: bool,
        score: int,
        reason: str,
        suggested_pitch: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO seen_jobs (
                    job_id, title, category, job_type, budget, url,
                    matched, score, reason, suggested_pitch, inserted_at, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["id"],
                    job.get("title", ""),
                    job.get("category", ""),
                    job.get("type_label", job.get("type", "")),
                    job.get("budget_display", ""),
                    job.get("url", ""),
                    1 if matched else 0,
                    int(score),
                    reason,
                    suggested_pitch,
                    job.get("inserted_at", ""),
                    now,
                ),
            )
            conn.commit()

    def get_stats(self) -> Dict[str, int]:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM seen_jobs").fetchone()["c"]
            matched = conn.execute(
                "SELECT COUNT(*) AS c FROM seen_jobs WHERE matched = 1"
            ).fetchone()["c"]
            return {"total_seen": total, "total_matched": matched}

    def get_recent_matches(self, limit: int = 5) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM seen_jobs
                WHERE matched = 1
                ORDER BY processed_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

