"""Employee roster import (CSV / XLSX) and helpers.

Every column of the roster becomes a template variable with the same name, so a
.docx containing {{ codice_fiscale }} is filled from the `codice_fiscale` column.
Required columns: full_name, email. `employee_id` is recommended; when missing it
is derived from the e-mail address.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

from .store import Store

REQUIRED = ("full_name", "email")
# Accepted header aliases → canonical column name (lower-cased, stripped).
ALIASES = {
    "name": "full_name", "nome": "full_name", "nome e cognome": "full_name", "nominativo": "full_name",
    "姓名": "full_name", "员工姓名": "full_name", "dipendente": "full_name",
    "e-mail": "email", "mail": "email", "邮箱": "email", "email aziendale": "email",
    "id": "employee_id", "matricola": "employee_id", "工号": "employee_id", "员工编号": "employee_id",
    "cf": "codice_fiscale", "codice fiscale": "codice_fiscale", "税号": "codice_fiscale",
    "mansione": "job_title", "ruolo": "job_title", "职位": "job_title", "岗位": "job_title",
    "reparto": "department", "部门": "department",
    "data assunzione": "hire_date", "data di assunzione": "hire_date", "入职日期": "hire_date",
}


def normalise_header(h: str) -> str:
    h = (h or "").strip()
    key = h.lower()
    if key in ALIASES:
        return ALIASES[key]
    h = unicodedata.normalize("NFKD", h)
    h = re.sub(r"[^\w]+", "_", h, flags=re.UNICODE).strip("_").lower()
    return h or "col"


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def read_roster(filename: str, content: bytes) -> list[dict]:
    """Parse CSV or XLSX bytes into a list of flat dicts with normalised keys."""
    rows: list[dict] = []
    if filename.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        headers = [normalise_header(_cell(h)) for h in next(it, [])]
        for raw in it:
            if raw is None or all(_cell(v) == "" for v in raw):
                continue
            rows.append({headers[i]: _cell(v) for i, v in enumerate(raw) if i < len(headers)})
    else:
        text = content.decode("utf-8-sig", errors="replace")
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t") if text.strip() else csv.excel
        reader = csv.reader(io.StringIO(text), dialect)
        headers = [normalise_header(h) for h in next(reader, [])]
        for raw in reader:
            if not raw or all(not c.strip() for c in raw):
                continue
            rows.append({headers[i]: c.strip() for i, c in enumerate(raw) if i < len(headers)})
    return rows


def import_rows(store: Store, rows: list[dict]) -> tuple[int, list[str]]:
    """Upsert roster rows. Returns (imported_count, errors)."""
    n, errors = 0, []
    for i, r in enumerate(rows, start=2):
        missing = [c for c in REQUIRED if not r.get(c)]
        if missing:
            errors.append(f"row {i}: missing {', '.join(missing)}")
            continue
        if "@" not in r["email"]:
            errors.append(f"row {i}: invalid e-mail '{r['email']}'")
            continue
        emp_id = r.get("employee_id") or r["email"].split("@")[0].lower()
        extra = {k: v for k, v in r.items() if k not in ("employee_id", "full_name", "email", "active")}
        active = str(r.get("active", "1")).strip().lower() not in ("0", "no", "false", "n", "inactive")
        store.upsert_employee(emp_id, r["full_name"], r["email"], extra, active)
        n += 1
    return n, errors


def import_file(store: Store, path: Path) -> tuple[int, list[str]]:
    return import_rows(store, read_roster(path.name, path.read_bytes()))


def employee_folder_name(emp: dict) -> str:
    name = re.sub(r"[^\w\- ]+", "_", emp["full_name"], flags=re.UNICODE).strip(" _")
    return f"{name}_{emp['employee_id']}"
