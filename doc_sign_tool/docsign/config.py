import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class SMTPConfig:
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    from_email: str = ""
    from_name: str = ""
    use_tls: bool = True      # STARTTLS on port 587
    use_ssl: bool = False     # implicit TLS on port 465

    @property
    def configured(self) -> bool:
        return bool(self.host and self.from_email)


@dataclass
class IMAPConfig:
    host: str = ""
    port: int = 993
    username: str = ""
    password: str = ""
    folder: str = "INBOX"
    use_ssl: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.host and self.username)


@dataclass
class Config:
    templates_dir: Path = BASE_DIR / "templates_docx"
    output_dir: Path = BASE_DIR / "output"
    data_dir: Path = BASE_DIR / "data"
    archive_dir: Path = BASE_DIR / "archive"
    convert_to_pdf: bool = True
    soffice_path: str = ""      # leave empty to auto-detect
    default_cc: list = field(default_factory=list)
    smtp: SMTPConfig = field(default_factory=SMTPConfig)
    imap: IMAPConfig = field(default_factory=IMAPConfig)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "records.db"


def _env(name: str, default=None):
    return os.environ.get(name, default)


def load_config(path: Path | None = None) -> Config:
    """Load config.yaml (if present) and override with DOCSIGN_* env vars."""
    cfg = Config()
    path = path or BASE_DIR / "config.yaml"
    raw = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    if "templates_dir" in raw:
        cfg.templates_dir = (BASE_DIR / raw["templates_dir"]).resolve()
    if "output_dir" in raw:
        cfg.output_dir = (BASE_DIR / raw["output_dir"]).resolve()
    if "data_dir" in raw:
        cfg.data_dir = (BASE_DIR / raw["data_dir"]).resolve()
    if "archive_dir" in raw:
        cfg.archive_dir = (BASE_DIR / raw["archive_dir"]).resolve()
    cfg.convert_to_pdf = bool(raw.get("convert_to_pdf", cfg.convert_to_pdf))
    cfg.soffice_path = raw.get("soffice_path", "") or ""
    cfg.default_cc = list(raw.get("default_cc", []) or [])

    s = raw.get("smtp", {}) or {}
    cfg.smtp = SMTPConfig(
        host=_env("DOCSIGN_SMTP_HOST", s.get("host", "")) or "",
        port=int(_env("DOCSIGN_SMTP_PORT", s.get("port", 587)) or 587),
        username=_env("DOCSIGN_SMTP_USERNAME", s.get("username", "")) or "",
        password=_env("DOCSIGN_SMTP_PASSWORD", s.get("password", "")) or "",
        from_email=_env("DOCSIGN_SMTP_FROM", s.get("from_email", "")) or "",
        from_name=_env("DOCSIGN_SMTP_FROM_NAME", s.get("from_name", "")) or "",
        use_tls=bool(s.get("use_tls", True)),
        use_ssl=bool(s.get("use_ssl", False)),
    )

    i = raw.get("imap", {}) or {}
    cfg.imap = IMAPConfig(
        host=_env("DOCSIGN_IMAP_HOST", i.get("host", "")) or "",
        port=int(_env("DOCSIGN_IMAP_PORT", i.get("port", 993)) or 993),
        username=_env("DOCSIGN_IMAP_USERNAME", i.get("username", cfg.smtp.username)) or "",
        password=_env("DOCSIGN_IMAP_PASSWORD", i.get("password", cfg.smtp.password)) or "",
        folder=i.get("folder", "INBOX") or "INBOX",
        use_ssl=bool(i.get("use_ssl", True)),
    )

    for d in (cfg.templates_dir, cfg.output_dir, cfg.data_dir, cfg.archive_dir):
        d.mkdir(parents=True, exist_ok=True)
    return cfg
