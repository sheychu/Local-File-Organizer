"""Local web UI: pick a template → fill the form → generate → send for signature.

Run:  python app.py        (then open http://127.0.0.1:5055)
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_file, url_for)
from werkzeug.utils import secure_filename

from docsign import service
from docsign.config import load_config
from docsign.store import Store
from docsign.templates import get_template, list_templates, write_sidecar_skeleton

cfg = load_config()
store = Store(cfg.db_path)

app = Flask(__name__, template_folder="web", static_folder=None)
app.secret_key = os.environ.get("DOCSIGN_SECRET", "local-only-dev-key")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024


@app.context_processor
def inject_globals():
    return {"smtp_ok": cfg.smtp.configured, "pdf_ok": cfg.convert_to_pdf}


def _parse_recipients(form) -> list[dict]:
    names = form.getlist("rcpt_name")
    emails = form.getlist("rcpt_email")
    roles = form.getlist("rcpt_role")
    return [{"name": n, "email": e, "role": r} for n, e, r in zip(names, emails, roles)]


def _parse_fields(spec, form) -> dict:
    data = {}
    for f in spec.fields:
        if f.type == "checkbox":
            data[f.name] = "Yes" if form.get(f.name) else "No"
        else:
            data[f.name] = form.get(f.name, "").strip()
    return data


@app.get("/")
def index():
    return render_template("index.html", templates=list_templates(cfg.templates_dir))


@app.get("/t/<name>")
def form(name):
    try:
        spec = get_template(cfg.templates_dir, name)
    except FileNotFoundError:
        abort(404)
    recipients = [vars(r) for r in spec.recipients] or [{"name": "", "email": "", "role": "signer"}]
    return render_template("form.html", spec=spec, data={}, recipients=recipients, errors={})


@app.post("/t/<name>/skeleton")
def skeleton(name):
    spec = get_template(cfg.templates_dir, name)
    p = write_sidecar_skeleton(spec)
    flash(f"已生成字段配置文件：{p.name}（在 templates_docx 目录中编辑标签/类型/默认收件人）")
    return redirect(url_for("form", name=name))


@app.post("/t/<name>/generate")
def generate(name):
    try:
        spec = get_template(cfg.templates_dir, name)
    except FileNotFoundError:
        abort(404)
    data = _parse_fields(spec, request.form)
    raw_rcpts = _parse_recipients(request.form)
    errors = service.validate(spec, data)
    recipients, rcpt_errors = service.normalise_recipients(raw_rcpts)
    if errors or rcpt_errors:
        for e in rcpt_errors:
            flash(e)
        return render_template("form.html", spec=spec, data=data, recipients=raw_rcpts, errors=errors), 400

    subject_tpl = request.form.get("email_subject") or None
    body_tpl = request.form.get("email_body") or None
    want_pdf = request.form.get("want_pdf") == "on"
    try:
        rec = service.generate(cfg, store, name, data, recipients, subject_tpl, body_tpl, want_pdf)
    except Exception as exc:
        flash(f"生成失败：{exc}")
        return render_template("form.html", spec=spec, data=data, recipients=raw_rcpts, errors={}), 500

    if want_pdf and not rec["pdf_path"]:
        flash("未找到 LibreOffice，无法转 PDF；已生成 .docx。")
    if request.form.get("action") == "generate_send":
        return _do_send(rec["id"], attach="pdf")
    return redirect(url_for("record", rec_id=rec["id"]))


@app.get("/r/<rec_id>")
def record(rec_id):
    rec = store.get(rec_id)
    if not rec:
        abort(404)
    return render_template("record.html", rec=rec, out_dir=Path(rec["docx_path"]).parent)


def _do_send(rec_id, attach, recipients=None, subject=None, body=None):
    try:
        result = service.send(cfg, store, rec_id, attach=attach, recipients=recipients, subject=subject, body=body)
    except Exception as exc:
        flash(f"发送失败：{exc}")
        return redirect(url_for("record", rec_id=rec_id))
    if result.get("sent"):
        flash("已通过 SMTP 发送给签字人。")
    else:
        flash("SMTP 未配置：已保存 .eml 文件，可用 Outlook / Thunderbird 打开后手动发送。")
    return redirect(url_for("record", rec_id=rec_id))


@app.post("/r/<rec_id>/send")
def send(rec_id):
    rec = store.get(rec_id)
    if not rec:
        abort(404)
    recipients, errs = service.normalise_recipients(_parse_recipients(request.form))
    for e in errs:
        flash(e)
    if errs:
        return redirect(url_for("record", rec_id=rec_id))
    return _do_send(rec_id, request.form.get("attach", "pdf"), recipients,
                    request.form.get("subject"), request.form.get("body"))


@app.post("/r/<rec_id>/signed")
def signed(rec_id):
    rec = store.get(rec_id)
    if not rec:
        abort(404)
    signed_path = None
    f = request.files.get("signed_file")
    if f and f.filename:
        dest = Path(rec["docx_path"]).parent / ("SIGNED_" + secure_filename(f.filename))
        f.save(dest)
        signed_path = str(dest)
    store.mark_signed(rec_id, signed_path, request.form.get("note") or None)
    flash("已标记为已签署。")
    return redirect(url_for("record", rec_id=rec_id))


@app.post("/r/<rec_id>/status")
def set_status(rec_id):
    store.set_status(rec_id, request.form.get("status", "generated"), request.form.get("note") or None)
    return redirect(url_for("record", rec_id=rec_id))


@app.get("/r/<rec_id>/file/<kind>")
def download(rec_id, kind):
    rec = store.get(rec_id)
    if not rec:
        abort(404)
    path = {"docx": rec["docx_path"], "pdf": rec["pdf_path"], "eml": rec["eml_path"],
            "signed": rec["signed_path"]}.get(kind)
    if not path or not Path(path).exists():
        abort(404)
    return send_file(path, as_attachment=True)


@app.get("/history")
def history():
    status = request.args.get("status") or None
    return render_template("history.html", records=store.list(status), status=status)


if __name__ == "__main__":
    port = int(os.environ.get("DOCSIGN_PORT", "5055"))
    print(f"DocSign running at http://127.0.0.1:{port}  (templates: {cfg.templates_dir})")
    app.run(host="127.0.0.1", port=port, debug=False)
