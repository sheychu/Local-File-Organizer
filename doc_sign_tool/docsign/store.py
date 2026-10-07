"""SQLite record of every generated document and its signature status."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

STATUSES = ("generated", "sent", "signed", "cancelled")

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    template TEXT NOT NULL,
    title TEXT,
    created_at TEXT NOT NULL,
    data_json TEXT NOT NULL,
    docx_path TEXT,
    pdf_path TEXT,
    recipients_json TEXT,
    subject TEXT,
    body TEXT,
    status TEXT NOT NULL,
    sent_at TEXT,
    message_id TEXT,
    eml_path TEXT,
    signed_at TEXT,
    signed_path TEXT,
    note TEXT
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def new_id() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]

    def create(self, rec_id: str, template: str, title: str, data: dict,
               docx_path: Path | None, pdf_path: Path | None,
               recipients: list[dict], subject: str, body: str) -> dict:
        with self._conn() as c:
            c.execute(
                "INSERT INTO records (id, template, title, created_at, data_json, docx_path, pdf_path, "
                "recipients_json, subject, body, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (rec_id, template, title, _now(), json.dumps(data, ensure_ascii=False),
                 str(docx_path) if docx_path else None, str(pdf_path) if pdf_path else None,
                 json.dumps(recipients, ensure_ascii=False), subject, body, "generated"),
            )
        return self.get(rec_id)

    def get(self, rec_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM records WHERE id = ?", (rec_id,)).fetchone()
        return self._hydrate(row)

    def list(self, status: str | None = None, limit: int = 200) -> list[dict]:
        q = "SELECT * FROM records"
        args: tuple = ()
        if status:
            q += " WHERE status = ?"
            args = (status,)
        q += " ORDER BY created_at DESC LIMIT ?"
        with self._conn() as c:
            rows = c.execute(q, args + (limit,)).fetchall()
        return [self._hydrate(r) for r in rows]

    def mark_sent(self, rec_id: str, message_id: str | None, eml_path: str | None, note: str | None,
                  recipients: list[dict], subject: str, body: str):
        with self._conn() as c:
            c.execute(
                "UPDATE records SET status='sent', sent_at=?, message_id=?, eml_path=?, note=?, "
                "recipients_json=?, subject=?, body=? WHERE id=?",
                (_now(), message_id, eml_path, note, json.dumps(recipients, ensure_ascii=False),
                 subject, body, rec_id),
            )

    def mark_signed(self, rec_id: str, signed_path: str | None, note: str | None = None):
        with self._conn() as c:
            c.execute("UPDATE records SET status='signed', signed_at=?, signed_path=?, note=COALESCE(?, note) WHERE id=?",
                      (_now(), signed_path, note, rec_id))

    def set_status(self, rec_id: str, status: str, note: str | None = None):
        if status not in STATUSES:
            raise ValueError(status)
        with self._conn() as c:
            c.execute("UPDATE records SET status=?, note=COALESCE(?, note) WHERE id=?", (status, note, rec_id))

    def delete(self, rec_id: str):
        with self._conn() as c:
            c.execute("DELETE FROM records WHERE id=?", (rec_id,))

    @staticmethod
    def _hydrate(row) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        d["data"] = json.loads(d.pop("data_json") or "{}")
        d["recipients"] = json.loads(d.pop("recipients_json") or "[]")
        return d
