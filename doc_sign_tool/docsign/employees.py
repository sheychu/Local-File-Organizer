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
    "e-mail": "email", "mail": "email", "邮箱": "email", "email aziendale": "email", "电子邮箱": "email",
    "邮件": "email", "电子邮件": "email", "工作邮箱": "email", "公司邮箱": "email",
    "id": "employee_id", "matricola": "employee_id", "工号": "employee_id", "员工编号": "employee_id",
    "员工id": "employee_id", "员工 id": "employee_id", "编号": "employee_id", "record id": "employee_id",
    "提交时间": "submitted_at", "提交人": "submitter", "手机": "phone", "手机号": "phone", "电话": "phone",
    "出生日期": "birth_date", "data di nascita": "birth_date", "住址": "address", "地址": "address", "indirizzo": "address",
    "合同类型": "contract_type", "tipo contratto": "contract_type", "工作地点": "sede_lavoro", "sede": "sede_lavoro",
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


def import_rows(store: Store, rows: list[dict]) -> tuple[int, list[str], list[str]]:
    """Upsert roster rows. Returns (imported_count, errors, imported_employee_ids)."""
    n, errors, ids = 0, [], []
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
        ids.append(emp_id)
    return n, errors, ids


def import_file(store: Store, path: Path) -> tuple[int, list[str], list[str]]:
    return import_rows(store, read_roster(path.name, path.read_bytes()))


def employee_folder_name(emp: dict) -> str:
    name = re.sub(r"[^\w\- ]+", "_", emp["full_name"], flags=re.UNICODE).strip(" _")
    return f"{name}_{emp['employee_id']}"


def export_with_status(store: Store, templates: list, dest: Path, fmt: str = "xlsx") -> Path:
    """Roster + one status column per template + last generation date. Paste back into Feishu/Excel."""
    emps = store.list_employees(include_inactive=True)
    latest = store.latest_by_employee_template()
    cols: list[str] = ["employee_id", "full_name", "email"]
    for emp in emps:
        for k in emp["extra"]:
            if k not in cols:
                cols.append(k)
    cols.append("active")
    status_cols = [(t.name, f"状态: {t.title}") for t in templates]
    header = cols + [lbl for _, lbl in status_cols] + ["最近生成", "最近签署"]
    rows = []
    for emp in emps:
        base = [emp["employee_id"], emp["full_name"], emp["email"]] + [emp["extra"].get(k, "") for k in cols[3:-1]] + [1 if emp["active"] else 0]
        recs = [latest.get((emp["employee_id"], t)) for t, _ in status_cols]
        statuses = [(r["status"] if r else "") for r in recs]
        gen = max((r["created_at"] for r in recs if r), default="")
        signed = max((r["signed_at"] or "" for r in recs if r), default="")
        rows.append(base + statuses + [gen[:10], signed[:10]])
    if fmt == "csv":
        with open(dest, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        return dest
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "名册+状态"
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDE3EA")
    for r in rows:
        ws.append(r)
    ws.freeze_panes = "D2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(dest)
    return dest
