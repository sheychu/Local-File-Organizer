"""Command-line interface. Examples:

  python cli.py list
  python cli.py fields nda_mutual
  python cli.py generate nda_mutual --data fill.json --to "Mario Rossi <mario@acme.it>" --send
  python cli.py send 20261007-101500-ab12cd --attach both
  python cli.py history --status sent
  python cli.py serve
"""
from __future__ import annotations

import argparse
import json
import sys
from email.utils import parseaddr
from pathlib import Path

from docsign import service
from docsign.config import load_config
from docsign.store import Store
from docsign.templates import get_template, list_templates, write_sidecar_skeleton


def _rcpt(s: str, role: str) -> dict:
    name, email = parseaddr(s)
    return {"name": name or email, "email": email, "role": role}


def main(argv=None):
    cfg = load_config()
    store = Store(cfg.db_path)
    ap = argparse.ArgumentParser(prog="docsign", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list templates")
    p = sub.add_parser("fields", help="show fields of a template"); p.add_argument("template")
    p = sub.add_parser("skeleton", help="write a starter .yaml for a template"); p.add_argument("template")

    p = sub.add_parser("generate", help="fill a template from JSON data")
    p.add_argument("template")
    p.add_argument("--data", required=True, help="JSON file with field values (or '-' for stdin)")
    p.add_argument("--to", action="append", default=[], help='signer, e.g. "Name <a@b.it>" (repeatable)')
    p.add_argument("--cc", action="append", default=[], help="cc recipient (repeatable)")
    p.add_argument("--no-pdf", action="store_true")
    p.add_argument("--send", action="store_true", help="send immediately after generating")

    p = sub.add_parser("send", help="(re)send a generated record")
    p.add_argument("record_id"); p.add_argument("--attach", choices=["pdf", "docx", "both"], default="pdf")

    p = sub.add_parser("signed", help="mark a record as signed")
    p.add_argument("record_id"); p.add_argument("--file", help="path of the signed copy")

    p = sub.add_parser("history", help="list records"); p.add_argument("--status")
    p = sub.add_parser("serve", help="start the web UI"); p.add_argument("--port", type=int, default=5055)

    a = ap.parse_args(argv)

    if a.cmd == "list":
        for t in list_templates(cfg.templates_dir):
            print(f"{t.name:30} {t.title:40} {len(t.fields)} fields  {t.description}")
    elif a.cmd == "fields":
        spec = get_template(cfg.templates_dir, a.template)
        for f in spec.fields:
            print(f"{f.name:30} {f.type:9} {'required' if f.required else 'optional':9} {f.label}")
        if spec.recipients:
            print("\nDefault recipients:")
            for r in spec.recipients:
                print(f"  {r.role:7} {r.name} <{r.email}>")
    elif a.cmd == "skeleton":
        print(write_sidecar_skeleton(get_template(cfg.templates_dir, a.template)))
    elif a.cmd == "generate":
        raw = sys.stdin.read() if a.data == "-" else Path(a.data).read_text(encoding="utf-8")
        data = json.loads(raw)
        spec = get_template(cfg.templates_dir, a.template)
        recipients = [_rcpt(s, "signer") for s in a.to] + [_rcpt(s, "cc") for s in a.cc]
        if not recipients:
            recipients = [vars(r) for r in spec.recipients]
        recipients, errs = service.normalise_recipients(recipients)
        if errs:
            sys.exit("\n".join(errs))
        rec = service.generate(cfg, store, a.template, data, recipients, want_pdf=not a.no_pdf)
        print(f"record: {rec['id']}\ndocx:   {rec['docx_path']}\npdf:    {rec['pdf_path']}")
        if a.send:
            print(service.send(cfg, store, rec["id"]))
    elif a.cmd == "send":
        print(service.send(cfg, store, a.record_id, attach=a.attach))
    elif a.cmd == "signed":
        store.mark_signed(a.record_id, a.file)
        print("ok")
    elif a.cmd == "history":
        for r in store.list(a.status):
            signers = ", ".join(x["name"] for x in r["recipients"] if x["role"] == "signer")
            print(f"{r['id']}  {r['status']:9}  {r['title'][:30]:30}  {signers}")
    elif a.cmd == "serve":
        import os
        os.environ["DOCSIGN_PORT"] = str(a.port)
        from app import app
        print(f"DocSign running at http://127.0.0.1:{a.port}")
        app.run(host="127.0.0.1", port=a.port)


if __name__ == "__main__":
    main()
