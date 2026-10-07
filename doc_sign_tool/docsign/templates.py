"""Discover .docx templates and build a form schema for each.

A template is `templates_docx/<name>.docx` containing Jinja placeholders such as
`{{ counterparty_name }}`. An optional sidecar `templates_docx/<name>.yaml`
adds labels, field types, defaults, default recipients and the e-mail text.
Fields found in the .docx but missing from the sidecar are still shown, as
plain text inputs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from docxtpl import DocxTemplate

from .filters import make_jinja_env

FIELD_TYPES = {"text", "textarea", "date", "number", "email", "select", "checkbox"}

# Placeholder names that are used by the e-mail body but are not document fields.
RESERVED = {"recipient_name", "sender_name", "attachment_name", "today", "today_it", "template_name", "title"}


@dataclass
class Field:
    name: str
    label: str = ""
    type: str = "text"
    required: bool = True
    default: str = ""
    help: str = ""
    options: list = field(default_factory=list)
    placeholder: str = ""

    def __post_init__(self):
        if not self.label:
            self.label = self.name.replace("_", " ").strip().capitalize()
        if self.type not in FIELD_TYPES:
            self.type = "text"


@dataclass
class Recipient:
    name: str = ""
    email: str = ""
    role: str = "signer"   # signer | cc


@dataclass
class TemplateSpec:
    name: str                # file stem, used in URLs
    path: Path
    title: str = ""
    description: str = ""
    fields: list[Field] = field(default_factory=list)
    recipients: list[Recipient] = field(default_factory=list)
    email_subject: str = "Please sign: {{ title }}"
    email_body: str = (
        "Dear {{ recipient_name }},\n\n"
        "Please find attached {{ attachment_name }} for your review and signature.\n"
        "Kindly sign and return a copy at your earliest convenience.\n\n"
        "Best regards,\n{{ sender_name }}"
    )
    output_filename: str = "{{ template_name }}_{{ counterparty_name | default('document') }}_{{ today }}"
    undeclared: list[str] = field(default_factory=list)   # fields in docx with no sidecar entry
    sidecar_path: Path | None = None

    @property
    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]


def scan_placeholders(docx_path: Path) -> set[str]:
    """Return the Jinja variable names used in a .docx template."""
    tpl = DocxTemplate(str(docx_path))
    try:
        names = tpl.get_undeclared_template_variables(jinja_env=make_jinja_env())
    except Exception as exc:  # malformed Jinja in the document
        raise ValueError(f"Template {docx_path.name} has invalid placeholders: {exc}") from exc
    return {n for n in names if n not in RESERVED}


def _load_sidecar(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_template(docx_path: Path) -> TemplateSpec:
    sidecar = docx_path.with_suffix(".yaml")
    meta = _load_sidecar(sidecar)
    spec = TemplateSpec(
        name=docx_path.stem,
        path=docx_path,
        title=meta.get("title") or docx_path.stem.replace("_", " "),
        description=meta.get("description", ""),
        sidecar_path=sidecar if sidecar.exists() else None,
    )
    if meta.get("email", {}).get("subject"):
        spec.email_subject = meta["email"]["subject"]
    if meta.get("email", {}).get("body"):
        spec.email_body = meta["email"]["body"]
    if meta.get("output_filename"):
        spec.output_filename = meta["output_filename"]

    declared: dict[str, Field] = {}
    for item in meta.get("fields", []) or []:
        if isinstance(item, str):
            item = {"name": item}
        f = Field(
            name=item["name"],
            label=item.get("label", ""),
            type=item.get("type", "text"),
            required=bool(item.get("required", True)),
            default=str(item.get("default", "") or ""),
            help=item.get("help", ""),
            options=list(item.get("options", []) or []),
            placeholder=item.get("placeholder", ""),
        )
        declared[f.name] = f

    found = scan_placeholders(docx_path)
    # Keep sidecar order first, then any undeclared placeholders alphabetically.
    ordered = [f for f in declared.values()]
    for name in sorted(found - set(declared)):
        ordered.append(Field(name=name))
        spec.undeclared.append(name)
    spec.fields = ordered

    for r in meta.get("recipients", []) or []:
        spec.recipients.append(
            Recipient(name=r.get("name", ""), email=r.get("email", ""), role=r.get("role", "signer"))
        )
    return spec


def list_templates(templates_dir: Path) -> list[TemplateSpec]:
    specs = []
    for p in sorted(templates_dir.glob("*.docx")):
        if p.name.startswith("~$"):  # Word lock files
            continue
        try:
            specs.append(load_template(p))
        except Exception as exc:
            specs.append(TemplateSpec(name=p.stem, path=p, title=p.stem, description=f"ERROR: {exc}"))
    return specs


def get_template(templates_dir: Path, name: str) -> TemplateSpec:
    if not re.fullmatch(r"[\w\-. ]+", name):
        raise FileNotFoundError(name)
    p = templates_dir / f"{name}.docx"
    if not p.exists():
        raise FileNotFoundError(name)
    return load_template(p)


def write_sidecar_skeleton(spec: TemplateSpec) -> Path:
    """Create a starter .yaml next to the .docx so the user can add labels/types."""
    target = spec.path.with_suffix(".yaml")
    if target.exists():
        return target
    data = {
        "title": spec.title,
        "description": "",
        "fields": [
            {"name": f.name, "label": f.label, "type": f.type, "required": True, "default": ""}
            for f in spec.fields
        ],
        "recipients": [{"name": "", "email": "", "role": "signer"}],
        "email": {"subject": spec.email_subject, "body": spec.email_body},
        "output_filename": spec.output_filename,
    }
    with open(target, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    return target
