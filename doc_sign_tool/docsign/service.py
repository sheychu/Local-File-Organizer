"""High-level operations shared by the web UI and the CLI."""
from __future__ import annotations

from pathlib import Path

from . import mailer, render
from .config import Config
from .filters import make_jinja_env
from .store import Store
from .inbox import subject_tag
from .templates import TemplateSpec, get_template


def validate(spec: TemplateSpec, data: dict) -> dict[str, str]:
    errors = {}
    for f in spec.fields:
        v = (data.get(f.name) or "").strip() if isinstance(data.get(f.name), str) else data.get(f.name)
        if f.required and (v in ("", None)) and f.type != "checkbox":
            errors[f.name] = "required"
        if f.type == "email" and v and "@" not in str(v):
            errors[f.name] = "invalid e-mail"
        if f.type == "number" and v not in ("", None):
            try:
                float(str(v).replace(",", "."))
            except ValueError:
                errors[f.name] = "not a number"
    return errors


def normalise_recipients(raw: list[dict]) -> tuple[list[dict], list[str]]:
    """Drop empty rows; return (recipients, errors)."""
    out, errors = [], []
    for r in raw:
        name = (r.get("name") or "").strip()
        email = (r.get("email") or "").strip()
        role = (r.get("role") or "signer").strip()
        if not name and not email:
            continue
        if "@" not in email:
            errors.append(f"Recipient '{name or email}' has no valid e-mail")
            continue
        out.append({"name": name or email, "email": email, "role": "cc" if role == "cc" else "signer"})
    return out, errors


def render_email(spec: TemplateSpec, data: dict, recipients: list[dict], attachment_name: str,
                 cfg: Config, subject_tpl: str | None = None, body_tpl: str | None = None) -> tuple[str, str]:
    env = make_jinja_env()
    ctx = render.build_context(spec, data)
    signers = [r for r in recipients if r["role"] == "signer"]
    ctx["recipient_name"] = ", ".join(r["name"] for r in signers) or "Sir/Madam"
    ctx["sender_name"] = cfg.smtp.from_name or cfg.smtp.from_email or ""
    ctx["attachment_name"] = attachment_name
    subject = env.from_string(subject_tpl or spec.email_subject).render(ctx).strip()
    body = env.from_string(body_tpl or spec.email_body).render(ctx)
    return subject, body


def generate(cfg: Config, store: Store, template_name: str, data: dict, recipients: list[dict],
             subject_tpl: str | None = None, body_tpl: str | None = None, want_pdf: bool | None = None,
             employee: dict | None = None, batch_id: str | None = None) -> dict:
    spec = get_template(cfg.templates_dir, template_name)
    if employee:
        # roster columns fill the template; explicit data (batch-level fields) wins
        data = {**employee["fields"], **{k: v for k, v in data.items() if v not in ("", None)}}
    # Sidecar defaults apply wherever nothing was supplied (CLI, batch, roster gaps).
    for f in spec.fields:
        if data.get(f.name) in ("", None) and f.default != "":
            data[f.name] = f.default
    errors = validate(spec, data)
    if errors:
        raise ValueError("; ".join(f"{k}: {v}" for k, v in errors.items()))
    rec_id = store.new_id()
    docx_path = render.render_docx(spec, data, cfg, rec_id)
    pdf_path = None
    if cfg.convert_to_pdf if want_pdf is None else want_pdf:
        pdf_path = render.convert_to_pdf(docx_path, cfg)
    attach = pdf_path or docx_path
    subject, body = render_email(spec, data, recipients, attach.name, cfg, subject_tpl, body_tpl)
    subject = f"{subject} {subject_tag(rec_id)}"
    return store.create(rec_id, spec.name, spec.title, data, docx_path, pdf_path, recipients, subject, body,
                        employee_id=employee["employee_id"] if employee else None,
                        employee_name=employee["full_name"] if employee else None, batch_id=batch_id)


def generate_for_employees(cfg: Config, store: Store, template_names: list[str], employee_ids: list[str],
                           batch_data: dict, want_pdf: bool | None = None, send_now: bool = False,
                           extra_cc: list[dict] | None = None) -> tuple[list[dict], list[str]]:
    """One document per (employee, template). Returns (records, errors)."""
    batch_id = store.new_id()
    records, errors = [], []
    for emp_id in employee_ids:
        emp = store.get_employee(emp_id)
        if not emp:
            errors.append(f"{emp_id}: not in roster")
            continue
        for tname in template_names:
            try:
                spec = get_template(cfg.templates_dir, tname)
                # signer = the employee; cc = template defaults + batch extras (deduplicated)
                recipients = [{"name": emp["full_name"], "email": emp["email"], "role": "signer"}]
                seen = {emp["email"].lower()}
                for r in [{"name": x.name, "email": x.email, "role": "cc"} for x in spec.recipients if x.role == "cc"] + list(extra_cc or []):
                    if r.get("email") and "@" in r["email"] and r["email"].lower() not in seen:
                        seen.add(r["email"].lower())
                        recipients.append({"name": r.get("name", ""), "email": r["email"], "role": "cc"})
                rec = generate(cfg, store, tname, dict(batch_data), recipients, want_pdf=want_pdf,
                               employee=emp, batch_id=batch_id)
                if send_now:
                    send(cfg, store, rec["id"])
                    rec = store.get(rec["id"])
                records.append(rec)
            except Exception as exc:
                errors.append(f"{emp['full_name']} / {tname}: {exc}")
    return records, errors


def batch_level_fields(cfg: Config, store: Store, template_names: list[str]) -> list:
    """Fields the templates need that the roster does not supply (asked once per batch)."""
    roster_cols: set[str] = set()
    for e in store.list_employees(include_inactive=True):
        roster_cols |= set(e["fields"])
    seen, out = set(), []
    for tname in template_names:
        spec = get_template(cfg.templates_dir, tname)
        for f in spec.fields:
            if f.name in roster_cols or f.name in seen:
                continue
            seen.add(f.name)
            out.append(f)
    return out


def send(cfg: Config, store: Store, rec_id: str, attach: str = "pdf",
         recipients: list[dict] | None = None, subject: str | None = None, body: str | None = None) -> dict:
    rec = store.get(rec_id)
    if not rec:
        raise FileNotFoundError(rec_id)
    recipients = recipients if recipients is not None else rec["recipients"]
    subject = subject if subject is not None else rec["subject"]
    if subject_tag(rec_id) not in subject:
        subject = f"{subject} {subject_tag(rec_id)}"
    body = body if body is not None else rec["body"]
    signers = [(r["name"], r["email"]) for r in recipients if r["role"] == "signer"]
    cc = [(r["name"], r["email"]) for r in recipients if r["role"] == "cc"]
    cc += [("", e) for e in cfg.default_cc if e and e not in {x[1] for x in cc}]
    if not signers:
        raise ValueError("At least one signer with an e-mail address is required")

    files: list[Path] = []
    if attach in ("pdf", "both") and rec["pdf_path"]:
        files.append(Path(rec["pdf_path"]))
    if attach in ("docx", "both") or (attach == "pdf" and not rec["pdf_path"]):
        files.append(Path(rec["docx_path"]))
    files = [f for f in files if f.exists()]
    if not files:
        raise FileNotFoundError("Generated file is missing on disk")

    msg = mailer.build_message(cfg, signers, cc, subject, body, files)
    result = mailer.send(cfg, msg, files[0].parent)
    store.mark_sent(rec_id, result.get("message_id"), result.get("eml"), result.get("note"),
                    recipients, subject, body)
    return result


def roster_gaps(cfg: Config, store: Store, template_names: list[str], employee_ids: list[str]) -> list[tuple[str, list[str]]]:
    """Required template fields that come from the roster but are empty for a selected employee."""
    roster_cols: set[str] = set()
    for e in store.list_employees(include_inactive=True):
        roster_cols |= set(e["fields"])
    needed: dict[str, str] = {}
    for tname in template_names:
        for f in get_template(cfg.templates_dir, tname).fields:
            if f.required and f.type != "checkbox" and f.name in roster_cols and f.default == "":
                needed[f.name] = f.label
    gaps = []
    for emp_id in employee_ids:
        emp = store.get_employee(emp_id)
        if not emp:
            continue
        missing = [lbl for name, lbl in needed.items() if not emp["fields"].get(name)]
        if missing:
            gaps.append((emp["full_name"], missing))
    return gaps
