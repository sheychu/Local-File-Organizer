"""Pull signed copies back from an IMAP mailbox.

Outgoing subjects carry a tag like `[DS-20261007-101010-4551d6]`. Replies that keep
the tag and carry a PDF/image attachment are matched to the record, archived and
marked signed.
"""
from __future__ import annotations

import email
import imaplib
import re
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from pathlib import Path

from .archive import archive_signed
from .config import Config
from .store import Store

TAG_RE = re.compile(r"\[DS-([0-9]{8}-[0-9]{6}-[0-9a-f]{6})\]")
ACCEPT_EXT = (".pdf", ".p7m", ".jpg", ".jpeg", ".png", ".docx")


def subject_tag(rec_id: str) -> str:
    return f"[DS-{rec_id}]"


def _decode(s) -> str:
    try:
        return str(make_header(decode_header(s or "")))
    except Exception:
        return s or ""


def fetch_signed(cfg: Config, store: Store, days: int = 30, mark_seen: bool = True) -> dict:
    im = cfg.imap
    if not im.configured:
        raise RuntimeError("IMAP not configured (config.yaml → imap)")
    M = imaplib.IMAP4_SSL(im.host, im.port) if im.use_ssl else imaplib.IMAP4(im.host, im.port)
    summary = {"checked": 0, "matched": 0, "archived": [], "skipped": []}
    try:
        M.login(im.username, im.password)
        M.select(im.folder)
        since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        typ, data = M.search(None, f'(SINCE {since} SUBJECT "[DS-")')
        ids = data[0].split() if typ == "OK" and data and data[0] else []
        for num in ids:
            typ, parts = M.fetch(num, "(RFC822)")
            if typ != "OK":
                continue
            msg = email.message_from_bytes(parts[0][1])
            summary["checked"] += 1
            m = TAG_RE.search(_decode(msg.get("Subject")))
            if not m:
                continue
            rec = store.get(m.group(1))
            if not rec:
                summary["skipped"].append(f"{m.group(1)}: unknown record")
                continue
            # Our own outgoing copy (e.g. in Sent, or a cc back to us) has no signed file to take.
            sender = email.utils.parseaddr(msg.get("From", ""))[1].lower()
            if sender and sender == (cfg.smtp.from_email or "").lower():
                continue
            attachments = []
            for part in msg.walk():
                fn = part.get_filename()
                if not fn:
                    continue
                fn = _decode(fn)
                if fn.lower().endswith(ACCEPT_EXT):
                    attachments.append((fn, part.get_payload(decode=True)))
            if not attachments:
                summary["skipped"].append(f"{rec['id']}: reply from {sender} without attachment")
                continue
            summary["matched"] += 1
            inbox_dir = Path(rec["docx_path"]).parent
            for fn, payload in attachments:
                safe = re.sub(r"[^\w\-. ]+", "_", fn)
                tmp = inbox_dir / f"RETURNED_{safe}"
                tmp.write_bytes(payload or b"")
                dest = archive_signed(cfg, store, rec, tmp, note=f"ricevuto via e-mail da {sender}")
                summary["archived"].append(str(dest))
            if mark_seen:
                M.store(num, "+FLAGS", "\\Seen")
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return summary
