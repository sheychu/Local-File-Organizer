"""Fill a .docx template and optionally convert it to PDF with LibreOffice."""
from __future__ import annotations

import datetime as dt
import re
import shutil
import subprocess
from pathlib import Path

from docxtpl import DocxTemplate, Listing

from .config import Config
from .filters import make_jinja_env
from .templates import TemplateSpec


def safe_filename(name: str) -> str:
    name = re.sub(r"[^\w\-. ]+", "_", name, flags=re.UNICODE).strip(" ._")
    return name[:120] or "document"


def build_context(spec: TemplateSpec, data: dict) -> dict:
    ctx = {f.name: data.get(f.name, f.default) for f in spec.fields}
    for k, v in data.items():
        ctx.setdefault(k, v)
    today = dt.date.today()
    ctx.setdefault("today", today.isoformat())
    ctx.setdefault("today_it", today.strftime("%d/%m/%Y"))
    ctx.setdefault("template_name", spec.name)
    ctx.setdefault("title", spec.title)
    return ctx


def render_docx(spec: TemplateSpec, data: dict, cfg: Config, record_id: str) -> Path:
    env = make_jinja_env(autoescape=True)   # values are inserted into XML
    ctx = build_context(spec, data)
    # Multi-line answers keep their line breaks inside the document.
    for f in spec.fields:
        if f.type == "textarea" and isinstance(ctx.get(f.name), str) and "\n" in ctx[f.name]:
            ctx[f.name] = Listing(ctx[f.name])
    tpl = DocxTemplate(str(spec.path))
    tpl.render(ctx, jinja_env=env)

    base = make_jinja_env().from_string(spec.output_filename).render({k: (str(v) if isinstance(v, Listing) else v) for k, v in ctx.items()})
    base = safe_filename(base)
    out_dir = cfg.output_dir / record_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{base}.docx"
    tpl.save(str(out))
    return out


def find_soffice(cfg: Config) -> str | None:
    if cfg.soffice_path:
        return cfg.soffice_path
    for cand in ("soffice", "libreoffice"):
        p = shutil.which(cand)
        if p:
            return p
    for cand in (
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ):
        if Path(cand).exists():
            return cand
    return None


def convert_to_pdf(docx_path: Path, cfg: Config) -> Path | None:
    """Return the PDF path, or None if LibreOffice is unavailable or fails."""
    exe = find_soffice(cfg)
    if not exe:
        return None
    out_dir = docx_path.parent
    try:
        subprocess.run(
            [exe, "--headless", "--norestore", "--convert-to", "pdf", "--outdir", str(out_dir), str(docx_path)],
            check=True, capture_output=True, timeout=180,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    pdf = docx_path.with_suffix(".pdf")
    return pdf if pdf.exists() else None
