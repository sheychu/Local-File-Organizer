# DocSign — 本地模板填充 + 发送签字工具

流程：**选模板 → 自动生成表单 → 填字段 → 生成 .docx / .pdf → 邮件发给签字人 → 本地记录状态（已生成 / 已发送 / 已签署）**

全部在本机运行，不依赖 DocuSign 等云服务。签字方式是把生成的 PDF 作为邮件附件发给签字人，对方签回后在记录页上传签署件并标记完成。

## 1. 安装

```bash
cd doc_sign_tool
pip install -r requirements.txt
cp config.example.yaml config.yaml     # 填 SMTP；不配也能用，会生成 .eml 文件供手动发送
python make_sample_template.py         # 生成示例模板 templates_docx/nda_mutual.docx（可选）
python app.py                          # 打开 http://127.0.0.1:5055
```

docx → PDF 需要本机安装 [LibreOffice](https://www.libreoffice.org/)；找不到时只生成 .docx，不报错。
Windows 上若没加到 PATH，在 config.yaml 里填 `soffice_path`。

## 2. 做一个模板

1. 用 Word 写好文件，要填的地方写成 `{{ field_name }}`，存到 `templates_docx/xxx.docx`。
   - 占位符要一次性打完，不要中途改格式，否则 Word 会把它拆成几段导致识别失败。拆了就全选该占位符 → 清除格式 → 重打。
   - 支持 Jinja 语法：`{% if penalty_amount %}…{% endif %}`、`{{ effective_date | date }}`（dd/mm/yyyy）、`{{ amount | money }}`（12.345,00 EUR）、`{{ notes | lines }}`（保留换行）。
   - 内置变量：`today`（2026-10-07）、`today_it`（07/10/2026）、`title`、`template_name`。
2. 不写 .yaml 也能用：所有占位符按文本框显示。要加中文标签、字段类型、默认值、默认收件人、邮件正文，
   在网页点「生成 .yaml 配置骨架」或执行 `python cli.py skeleton xxx`，然后编辑 `templates_docx/xxx.yaml`：

```yaml
title: Mutual NDA (EN/IT)
fields:
  - {name: counterparty_name, label: 对方公司名称}
  - {name: effective_date, label: 生效日期, type: date}
  - {name: governing_law, label: 适用法律, type: select, options: [Italy, Spain], default: Italy}
  - {name: penalty_amount, label: 违约金, type: number, required: false}
  - {name: purpose, label: 合作目的, type: textarea}
recipients:
  - {name: "", email: "", role: signer}             # 签字人（发件时填）
  - {name: Legal, email: legal@company.it, role: cc} # 固定抄送
email:
  subject: "Firma richiesta: NDA {{ company_name }} – {{ counterparty_name }}"
  body: |
    Gentile {{ recipient_name }}, in allegato {{ attachment_name }} …
output_filename: "NDA_{{ counterparty_name }}_{{ today }}"
```

字段类型：`text` `textarea` `date` `number` `email` `select` `checkbox`。
邮件主题/正文可用和文档相同的占位符，另有 `recipient_name`、`attachment_name`、`sender_name`。

## 3. 使用

**网页**：首页选模板 → 填表 → 填签字人邮箱 → 「生成并发送签字」。记录页可下载文件、重发、改收件人、上传签回件并标记 `signed`。「记录」页按状态筛选，一眼看到哪些还没签回。

**命令行**（适合批量）：
```bash
python cli.py list
python cli.py fields nda_mutual
python cli.py generate nda_mutual --data fill.json --to "Mario Rossi <mario@acme.it>" --cc legal@company.it --send
python cli.py send <record_id> --attach both
python cli.py signed <record_id> --file ~/Downloads/nda_firmato.pdf
python cli.py history --status sent
```

## 4. 数据存放

| 内容 | 位置 |
|---|---|
| 模板 + 字段配置 | `templates_docx/*.docx` + `*.yaml`（纳入 git） |
| 生成的文件、.eml、签回件 | `output/<record_id>/`（git 忽略） |
| 状态记录 | `data/records.db`（SQLite，git 忽略） |
| SMTP 密码 | `config.yaml`（git 忽略）或环境变量 `DOCSIGN_SMTP_PASSWORD` |

## 5. SMTP 提示

- Gmail / Google Workspace：`smtp.gmail.com:587`，用户名为邮箱，密码用「应用专用密码」。
- Microsoft 365 / Outlook：`smtp.office365.com:587`。公司租户若禁用了 SMTP AUTH，让 IT 开启或改用 .eml 手动发送。
- 不配置 SMTP：每次发送会在 `output/<id>/message.eml` 生成完整邮件，双击用 Outlook / Thunderbird 打开后点发送即可，状态同样记为 `sent`。

## 6. 结构

```
doc_sign_tool/
├── app.py                 Flask 网页
├── cli.py                 命令行
├── docsign/
│   ├── templates.py       扫描 .docx 占位符 + 读 .yaml → 表单 schema
│   ├── render.py          填充 docx、LibreOffice 转 PDF
│   ├── filters.py         Jinja 过滤器 date / money / lines
│   ├── mailer.py          SMTP 发送 / .eml 回退
│   ├── store.py           SQLite 记录
│   └── service.py         生成、发送、校验（网页和 CLI 共用）
├── web/                   HTML 页面
└── templates_docx/        你的模板
```
