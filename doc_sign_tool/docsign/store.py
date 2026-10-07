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
    note TEXT,
    employee_id TEXT,
    employee_name TEXT,
    archive_path TEXT,
    batch_id TEXT
);
CREATE TABLE IF NOT EXISTS employees (
    employee_id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    extra_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
"""

# Columns added after the first release; applied with ALTER TABLE on old databases.
_MIGRATIONS = [
    ("records", "employee_id", "TEXT"),
    ("records", "employee_name", "TEXT"),
    ("records", "archive_path", "TEXT"),
    ("records", "batch_id", "TEXT"),
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        with self._conn() as c:
            c.executescript(SCHEMA)
            for table, col, typ in _MIGRATIONS:
                cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
                if col not in cols:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")

    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def new_id() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]

    def create(self, rec_id: str, template: str, title: str, data: dict,
               docx_path: Path | None, pdf_path: Path | None,
               recipients: list[dict], subject: str, body: str,
               employee_id: str | None = None, employee_name: str | None = None,
               batch_id: str | None = None) -> dict:
        with self._conn() as c:
            c.execute(
                "INSERT INTO records (id, template, title, created_at, data_json, docx_path, pdf_path, "
                "recipients_json, subject, body, status, employee_id, employee_name, batch_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rec_id, template, title, _now(), json.dumps(data, ensure_ascii=False),
                 str(docx_path) if docx_path else None, str(pdf_path) if pdf_path else None,
                 json.dumps(recipients, ensure_ascii=False), subject, body, "generated",
                 employee_id, employee_name, batch_id),
            )
        return self.get(rec_id)

    def get(self, rec_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM records WHERE id = ?", (rec_id,)).fetchone()
        return self._hydrate(row)

    def list(self, status: str | None = None, limit: int = 500, employee_id: str | None = None,
             template: str | None = None, batch_id: str | None = None) -> list[dict]:
        q = "SELECT * FROM records WHERE 1=1"
        args: list = []
        for col, val in (("status", status), ("employee_id", employee_id), ("template", template), ("batch_id", batch_id)):
            if val:
                q += f" AND {col} = ?"
                args.append(val)
        args = tuple(args)
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

    def mark_signed(self, rec_id: str, signed_path: str | None, note: str | None = None,
                    archive_path: str | None = None):
        with self._conn() as c:
            c.execute("UPDATE records SET status='signed', signed_at=?, signed_path=COALESCE(?, signed_path), "
                      "archive_path=COALESCE(?, archive_path), note=COALESCE(?, note) WHERE id=?",
                      (_now(), signed_path, archive_path, note, rec_id))

    def latest_by_employee_template(self) -> dict[tuple[str, str], dict]:
        """For the compliance matrix: most recent non-cancelled record per (employee, template)."""
        out: dict[tuple[str, str], dict] = {}
        for r in self.list(limit=100000):
            if not r["employee_id"] or r["status"] == "cancelled":
                continue
            key = (r["employee_id"], r["template"])
            # list() is newest-first; prefer a signed record over a newer unsigned one.
            cur = out.get(key)
            if cur is None or (r["status"] == "signed" and cur["status"] != "signed"):
                out[key] = r
        return out

    # ---------------- employees ----------------
    def upsert_employee(self, employee_id: str, full_name: str, email: str, extra: dict | None = None,
                        active: bool = True):
        with self._conn() as c:
            c.execute(
                "INSERT INTO employees (employee_id, full_name, email, active, extra_json, updated_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(employee_id) DO UPDATE SET full_name=excluded.full_name, "
                "email=excluded.email, active=excluded.active, extra_json=excluded.extra_json, updated_at=excluded.updated_at",
                (employee_id, full_name, email, 1 if active else 0, json.dumps(extra or {}, ensure_ascii=False), _now()),
            )

    def get_employee(self, employee_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM employees WHERE employee_id=?", (employee_id,)).fetchone()
        return self._hydrate_employee(row)

    def list_employees(self, include_inactive: bool = False) -> list[dict]:
        q = "SELECT * FROM employees" + ("" if include_inactive else " WHERE active=1") + " ORDER BY full_name"
        with self._conn() as c:
            rows = c.execute(q).fetchall()
        return [self._hydrate_employee(r) for r in rows]

    def delete_employee(self, employee_id: str):
        with self._conn() as c:
            c.execute("DELETE FROM employees WHERE employee_id=?", (employee_id,))

    @staticmethod
    def _hydrate_employee(row) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        extra = json.loads(d.pop("extra_json") or "{}")
        d["active"] = bool(d["active"])
        d["extra"] = extra
        # Flat view: every roster column is available as a template variable.
        d["fields"] = {**extra, "employee_id": d["employee_id"], "full_name": d["full_name"], "email": d["email"]}
        return d

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
