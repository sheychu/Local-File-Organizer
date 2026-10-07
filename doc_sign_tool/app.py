"""Local web UI: pick a template → fill the form → generate → send for signature.

Run:  python app.py        (then open http://127.0.0.1:5055)
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_file, url_for)
from werkzeug.utils import secure_filename

from docsign import archive, employees, inbox, service
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
    return {"smtp_ok": cfg.smtp.configured, "imap_ok": cfg.imap.configured, "pdf_ok": cfg.convert_to_pdf}


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
    f = request.files.get("signed_file")
    if f and f.filename:
        dest = Path(rec["docx_path"]).parent / ("SIGNED_" + secure_filename(f.filename))
        f.save(dest)
        archived = archive.archive_signed(cfg, store, rec, dest, request.form.get("note") or None)
        flash(f"已标记为已签署，签署件归档至 {archived}")
    else:
        store.mark_signed(rec_id, None, request.form.get("note") or None)
        flash("已标记为已签署（未上传签署件）。")
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
            "signed": rec["archive_path"] or rec["signed_path"]}.get(kind)
    if not path or not Path(path).exists():
        abort(404)
    return send_file(path, as_attachment=True)


@app.get("/history")
def history():
    status = request.args.get("status") or None
    emp = request.args.get("employee_id") or None
    batch_id = request.args.get("batch_id") or None
    return render_template("history.html", records=store.list(status, employee_id=emp, batch_id=batch_id),
                           status=status, employee_id=emp, batch_id=batch_id)


# ---------------- employees ----------------
@app.get("/employees")
def employees_page():
    return render_template("employees.html", employees=store.list_employees(include_inactive=True))


@app.post("/employees/import")
def employees_import():
    f = request.files.get("roster")
    if not f or not f.filename:
        flash("请选择 CSV 或 XLSX 文件。")
        return redirect(url_for("employees_page"))
    try:
        rows = employees.read_roster(f.filename, f.read())
        n, errs = employees.import_rows(store, rows)
    except Exception as exc:
        flash(f"导入失败：{exc}")
        return redirect(url_for("employees_page"))
    flash(f"已导入 / 更新 {n} 名员工。" + (f" {len(errs)} 行跳过：" + "; ".join(errs[:5]) if errs else ""))
    return redirect(url_for("employees_page"))


@app.post("/employees/add")
def employees_add():
    fm = request.form
    if not fm.get("full_name") or "@" not in fm.get("email", ""):
        flash("姓名和有效邮箱必填。")
        return redirect(url_for("employees_page"))
    extra = {}
    for k, v in zip(fm.getlist("extra_key"), fm.getlist("extra_val")):
        if k.strip():
            extra[employees.normalise_header(k)] = v.strip()
    for k in ("codice_fiscale", "job_title", "department", "hire_date"):
        if fm.get(k):
            extra[k] = fm[k].strip()
    emp_id = fm.get("employee_id", "").strip() or fm["email"].split("@")[0].lower()
    store.upsert_employee(emp_id, fm["full_name"].strip(), fm["email"].strip(), extra, fm.get("active", "1") == "1")
    flash(f"已保存 {fm['full_name']}。")
    return redirect(url_for("employees_page"))


@app.post("/employees/<emp_id>/delete")
def employees_delete(emp_id):
    store.delete_employee(emp_id)
    flash("已删除。生成过的记录保留。")
    return redirect(url_for("employees_page"))


# ---------------- batch ----------------
@app.route("/batch", methods=["GET", "POST"])
def batch():
    templates = [t for t in list_templates(cfg.templates_dir) if not t.description.startswith("ERROR")]
    emps = store.list_employees()
    sel_t = request.form.getlist("templates") if request.method == "POST" else []
    sel_e = request.form.getlist("employees") if request.method == "POST" else []
    step = request.form.get("step", "1")
    if request.method == "POST" and step == "2":
        if not sel_t or not sel_e:
            flash("至少选一个模板和一名员工。")
            step = "1"
        else:
            fields = service.batch_level_fields(cfg, store, sel_t)
            return render_template("batch.html", templates=templates, employees=emps, sel_t=sel_t, sel_e=sel_e,
                                   step="2", fields=fields, cc=cfg.default_cc,
                                   gaps=service.roster_gaps(cfg, store, sel_t, sel_e))
    if request.method == "POST" and step == "3":
        fields = service.batch_level_fields(cfg, store, sel_t)
        batch_data = {f.name: request.form.get(f.name, "").strip() for f in fields}
        missing = [f.label for f in fields if f.required and f.type != "checkbox" and not batch_data.get(f.name)]
        if missing:
            flash("必填：" + "、".join(missing))
            return render_template("batch.html", templates=templates, employees=emps, sel_t=sel_t, sel_e=sel_e,
                                   step="2", fields=fields, cc=cfg.default_cc, data=batch_data)
        cc = [{"name": "", "email": e.strip(), "role": "cc"} for e in request.form.get("cc", "").replace(";", ",").split(",") if "@" in e]
        send_now = request.form.get("action") == "generate_send"
        recs, errs = service.generate_for_employees(cfg, store, sel_t, sel_e, batch_data,
                                                    want_pdf=request.form.get("want_pdf") == "on",
                                                    send_now=send_now, extra_cc=cc)
        for e in errs:
            flash(e)
        if recs:
            flash(f"已生成 {len(recs)} 份文件" + ("并发送。" if send_now else "。") +
                  ("" if cfg.smtp.configured or not send_now else " SMTP 未配置：每份都保存了 .eml。"))
            return redirect(url_for("history", batch_id=recs[0]["batch_id"]))
        return redirect(url_for("batch"))
    return render_template("batch.html", templates=templates, employees=emps, sel_t=sel_t, sel_e=sel_e, step="1")


# ---------------- dashboard / archive ----------------
@app.get("/dashboard")
def dashboard():
    templates = [t for t in list_templates(cfg.templates_dir) if not t.description.startswith("ERROR")]
    emps = store.list_employees()
    latest = store.latest_by_employee_template()
    counts = {"signed": 0, "sent": 0, "generated": 0, "missing": 0}
    for e in emps:
        for t in templates:
            r = latest.get((e["employee_id"], t.name))
            counts[r["status"] if r else "missing"] = counts.get(r["status"] if r else "missing", 0) + 1
    return render_template("dashboard.html", templates=templates, employees=emps, latest=latest, counts=counts,
                           archive_dir=cfg.archive_dir)


@app.post("/inbox/fetch")
def inbox_fetch():
    try:
        s = inbox.fetch_signed(cfg, store, days=int(request.form.get("days", 30)))
    except Exception as exc:
        flash(f"拉取失败：{exc}")
        return redirect(url_for("dashboard"))
    flash(f"检查 {s['checked']} 封邮件，匹配 {s['matched']} 份签回件，归档 {len(s['archived'])} 个文件。"
          + (" 跳过：" + "; ".join(s["skipped"][:5]) if s["skipped"] else ""))
    return redirect(url_for("dashboard"))


@app.post("/register/export")
def register_export():
    fmt = request.form.get("fmt", "xlsx")
    path = archive.export_register(cfg, store, fmt)
    return send_file(path, as_attachment=True)


if __name__ == "__main__":
    port = int(os.environ.get("DOCSIGN_PORT", "5055"))
    print(f"DocSign running at http://127.0.0.1:{port}  (templates: {cfg.templates_dir})")
    app.run(host="127.0.0.1", port=port, debug=False)
