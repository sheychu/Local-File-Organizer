"""Command-line interface. Examples:

  python cli.py list
  python cli.py fields nda_mutual
  python cli.py generate nda_mutual --data fill.json --to "Mario Rossi <mario@acme.it>" --send
  python cli.py send 20261007-101500-ab12cd --attach both
  python cli.py history --status sent
  python cli.py serve

Employee / compliance flow:
  python cli.py employees import samples/employees_sample.csv
  python cli.py employees list
  python cli.py batch informativa_privacy_dipendenti consegna_policy --all --set policy_version=2.0 --send
  python cli.py inbox --days 30          # pull signed replies from IMAP, archive, mark signed
  python cli.py matrix                   # employee × document status
  python cli.py register --fmt xlsx      # export the filing register (备案登记表)
"""
from __future__ import annotations

import argparse
import json
import sys
from email.utils import parseaddr
from pathlib import Path

from docsign import archive, employees, inbox, service
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

    p = sub.add_parser("employees", help="roster: import <file> | list")
    p.add_argument("op", choices=["import", "list"]); p.add_argument("file", nargs="?")

    p = sub.add_parser("batch", help="generate one document per employee per template")
    p.add_argument("templates", nargs="+")
    p.add_argument("--all", action="store_true", help="all active employees")
    p.add_argument("--emp", action="append", default=[], help="employee_id (repeatable)")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="batch-level field")
    p.add_argument("--cc", action="append", default=[])
    p.add_argument("--no-pdf", action="store_true"); p.add_argument("--send", action="store_true")

    p = sub.add_parser("inbox", help="fetch signed replies via IMAP"); p.add_argument("--days", type=int, default=30)
    p = sub.add_parser("archive", help="archive a signed file for a record"); p.add_argument("record_id"); p.add_argument("file")
    sub.add_parser("matrix", help="employee × document status")
    p = sub.add_parser("register", help="export filing register"); p.add_argument("--fmt", choices=["xlsx", "csv"], default="xlsx")

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
    elif a.cmd == "employees":
        if a.op == "import":
            if not a.file:
                sys.exit("employees import <file.csv|xlsx>")
            n, errs = employees.import_file(store, Path(a.file))
            print(f"imported {n}" + (f", {len(errs)} skipped:\n  " + "\n  ".join(errs) if errs else ""))
        else:
            for e in store.list_employees(include_inactive=True):
                print(f"{e['employee_id']:12} {e['full_name']:30} {e['email']:35} {'' if e['active'] else 'INACTIVE'}")
    elif a.cmd == "batch":
        ids = [e["employee_id"] for e in store.list_employees()] if a.all else a.emp
        if not ids:
            sys.exit("choose --all or --emp <id>")
        data = dict(kv.split("=", 1) for kv in a.set)
        missing = [f.label for f in service.batch_level_fields(cfg, store, a.templates)
                   if f.required and f.type != "checkbox" and not (data.get(f.name) or f.default)]
        if missing:
            sys.exit("batch-level fields required via --set: " + ", ".join(missing))
        cc = [_rcpt(s, "cc") for s in a.cc]
        recs, errs = service.generate_for_employees(cfg, store, a.templates, ids, data, want_pdf=not a.no_pdf,
                                                    send_now=a.send, extra_cc=cc)
        for r in recs:
            print(f"{r['id']}  {r['status']:9}  {r['employee_name']:28}  {r['title']}")
        for e in errs:
            print("ERROR", e, file=sys.stderr)
        print(f"{len(recs)} generated, {len(errs)} errors")
    elif a.cmd == "inbox":
        s = inbox.fetch_signed(cfg, store, days=a.days)
        print(f"checked {s['checked']}, matched {s['matched']}, archived {len(s['archived'])}")
        for p in s["archived"]:
            print("  ", p)
        for p in s["skipped"]:
            print("  skipped:", p)
    elif a.cmd == "archive":
        rec = store.get(a.record_id) or sys.exit("unknown record")
        print(archive.archive_signed(cfg, store, rec, Path(a.file)))
    elif a.cmd == "matrix":
        templates = [t.name for t in list_templates(cfg.templates_dir)]
        latest = store.latest_by_employee_template()
        print(f"{'employee':30} " + " ".join(f"{t[:18]:18}" for t in templates))
        for e in store.list_employees():
            cells = [(latest.get((e["employee_id"], t)) or {}).get("status", "-") for t in templates]
            print(f"{e['full_name'][:30]:30} " + " ".join(f"{c:18}" for c in cells))
    elif a.cmd == "register":
        print(archive.export_register(cfg, store, a.fmt))
    elif a.cmd == "serve":
        import os
        os.environ["DOCSIGN_PORT"] = str(a.port)
        from app import app
        print(f"DocSign running at http://127.0.0.1:{a.port}")
        app.run(host="127.0.0.1", port=a.port)


if __name__ == "__main__":
    main()
