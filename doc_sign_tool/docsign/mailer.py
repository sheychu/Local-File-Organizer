"""Send the generated document to the signatories by e-mail (SMTP).

When SMTP is not configured the message is written as an .eml file next to the
document instead, so the whole flow can be tested offline and the .eml can be
opened in Outlook/Thunderbird and sent by hand.
"""
from __future__ import annotations

import mimetypes
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

from .config import Config


def build_message(cfg: Config, to: list[tuple[str, str]], cc: list[tuple[str, str]],
                  subject: str, body: str, attachments: list[Path]) -> EmailMessage:
    msg = EmailMessage()
    sender = formataddr((cfg.smtp.from_name, cfg.smtp.from_email)) if cfg.smtp.from_email else "docsign@localhost"
    msg["From"] = sender
    msg["To"] = ", ".join(formataddr((n, e)) for n, e in to)
    if cc:
        msg["Cc"] = ", ".join(formataddr((n, e)) for n, e in cc)
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain="docsign.local")
    msg.set_content(body)
    for path in attachments:
        ctype, _ = mimetypes.guess_type(str(path))
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        with open(path, "rb") as f:
            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename=path.name)
    return msg


def send(cfg: Config, msg: EmailMessage, fallback_dir: Path) -> dict:
    """Send via SMTP; if not configured, save as .eml. Returns a status dict."""
    if not cfg.smtp.configured:
        eml = fallback_dir / "message.eml"
        with open(eml, "wb") as f:
            f.write(bytes(msg))
        return {"sent": False, "eml": str(eml), "message_id": msg["Message-ID"],
                "note": "SMTP not configured; saved .eml for manual sending."}

    s = cfg.smtp
    if s.use_ssl:
        server = smtplib.SMTP_SSL(s.host, s.port, timeout=60)
    else:
        server = smtplib.SMTP(s.host, s.port, timeout=60)
    try:
        server.ehlo()
        if s.use_tls and not s.use_ssl:
            server.starttls()
            server.ehlo()
        if s.username:
            server.login(s.username, s.password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return {"sent": True, "message_id": msg["Message-ID"]}
