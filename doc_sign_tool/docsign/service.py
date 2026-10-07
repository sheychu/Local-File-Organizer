"""High-level operations shared by the web UI and the CLI."""
from __future__ import annotations

from pathlib import Path

from . import mailer, render
from .config import Config
from .filters import make_jinja_env
from .store import Store
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
             subject_tpl: str | None = None, body_tpl: str | None = None, want_pdf: bool | None = None) -> dict:
    spec = get_template(cfg.templates_dir, template_name)
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
    return store.create(rec_id, spec.name, spec.title, data, docx_path, pdf_path, recipients, subject, body)


def send(cfg: Config, store: Store, rec_id: str, attach: str = "pdf",
         recipients: list[dict] | None = None, subject: str | None = None, body: str | None = None) -> dict:
    rec = store.get(rec_id)
    if not rec:
        raise FileNotFoundError(rec_id)
    recipients = recipients if recipients is not None else rec["recipients"]
    subject = subject if subject is not None else rec["subject"]
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
