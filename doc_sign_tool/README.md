# DocSign — 员工合规文件：批量生成 → 发送签署 → 签回归档

典型场景：**导入员工名册 → 选员工 × 选合规文件 → 每人一份自动填好 → 逐人邮件发送签字 → 签回的 PDF 自动归档到员工文件夹 → 导出备案登记表 / 看板看谁还没签**。

全部在本机运行，不依赖 DocuSign 等云服务。签字方式是把 PDF 作为邮件附件发给员工，员工签后回复邮件（附 PDF）；工具通过 IMAP 拉取回信、按主题里的 `[DS-编号]` 匹配记录、归档并标记 `signed`。也可手动上传签回件。

## 0. 五分钟跑通员工流程

```bash
cd doc_sign_tool
pip install -r requirements.txt
python make_sample_template.py                              # 示例模板：隐私告知书、政策签收单、NDA
python cli.py employees import samples/employees_sample.csv # 示例名册 3 人
python app.py                                               # http://127.0.0.1:5055
```

网页：「员工」页导入真实名册 → 「批量生成」选文件 + 选人 → 填本批次统一字段（公司名、版本号…）→ 「生成并逐人发送」→ 「看板」看每人每份的状态 → 收到签回件点「从邮箱拉取签回件」或在记录页手动上传 → 「导出备案登记表 .xlsx」。

### 飞书表单 → Base → 本工具（推荐流程）

1. 飞书表单收集员工信息，进 Base。Base 右上角「…」→ 导出为 Excel。
2. 「员工」页上传，点 **「导入并去批量生成」**：刚导入的人自动预选。
3. 选文件 → 下一步。页面顶部「已生成检查」显示 *N / M 已有文件*，并标出名册数据变更的人。
   - **跳过已生成**（默认勾选）：同一张表反复导出导入，不会重复生成；只有新进名册的人会生成。
   - **名册数据变更的重新生成**：某人职位、地址在飞书里改过，勾上就重出这个人的文件，其他人照旧跳过。
4. 生成后点「导出名册 + 状态列」，得到原列 + `状态: <文件名>` 列 + 最近生成 / 签署日期，粘回 Base 的「已生成」字段即可。

幂等的键是 **(员工ID, 文件)**。所以飞书表里一定要有稳定的员工ID列（工号 / 员工ID / Matricola）；没有的话用邮箱前缀代替，邮箱改了就会被当成新人。

### 员工名册

CSV（`,` `;` 或 Tab 分隔）或 Excel。**每一列都成为模板变量**：列 `codice_fiscale` ↔ 模板 `{{ codice_fiscale }}`。
必填列 `full_name`、`email`；建议 `employee_id`（工号/matricola，用于更新去重和归档文件夹名）。
常见中文/意大利语表头自动映射：姓名/Nome e cognome → full_name，邮箱/电子邮箱/E-mail → email，工号/员工ID/Matricola → employee_id，Codice fiscale → codice_fiscale，职位/Mansione → job_title，部门/Reparto → department，入职日期/Data assunzione → hire_date，工作地点/Sede → sede_lavoro，提交时间 → submitted_at，手机 → phone。其他列原样保留（小写、空格转下划线）。飞书导出的日期 `2026/10/01` 和 Excel 日期都能被 `| date` 过滤器格式化。
重复导入按 `employee_id` 更新。`active` 列填 0/no 表示离职（看板和批量不再出现，历史记录保留）。

### 批量生成的字段来源

| 模板占位符 | 取值 |
|---|---|
| 名册里有的列（姓名、CF、职位、入职日期…） | 每个员工自己的值 |
| 名册里没有的（公司名、政策版本、DPO 邮箱…） | 批量页第 2 步填一次，全批次共用；.yaml 里的 `default` 自动带入 |
| 名册有列但某人为空且字段必填 | 第 2 步红框提示「名册缺字段」，补齐后再生成 |

收件人：签字人 = 员工本人；抄送 = 模板 .yaml 里 `role: cc` 的默认抄送 + 批量页填的抄送 + config 里的 `default_cc`。

### 签回件归档

- 自动：config.yaml 配好 `imap`，看板点「从邮箱拉取签回件」（或 `python cli.py inbox`）。匹配规则：回信主题含 `[DS-编号]` 且带 .pdf/.p7m/.jpg/.png/.docx 附件；发件人是自己的邮件（已发送副本）会跳过。
- 手动：记录页上传文件 → 同样归档。
- 归档路径：`archive/<姓名>_<工号>/<文件名>_<日期>_SIGNED.pdf`。
- 备案登记表：`archive/registro_firme_<日期>.xlsx`，两个 sheet：逐份记录（含归档路径）+ 员工 × 文件矩阵。

### 证据效力提示

员工扫描签字后回邮的 PDF，对 informativa privacy、policy 签收这类只需证明"已交付/已知悉"的文件足够。对需要 forma scritta 的文件（patto di prova art. 2096 c.c.、patto di non concorrenza art. 2125 c.c. 等），建议改用 FEA / firma digitale（.p7m 可直接归档）或收回纸质原件，工具层面不变。

---

## 单份文件模式（非员工场景）

首页「模板」选一个 → 手填所有字段 → 填签字人邮箱 → 生成并发送。适合 NDA、供应商函件等对外文件。

## 1. 安装与配置

```bash
cp config.example.yaml config.yaml     # 填 SMTP（发信）和 IMAP（收签回件）；都不配也能用：发送时生成 .eml 供手动发送
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

**命令行**：
```bash
# 员工流程
python cli.py employees import roster.xlsx
python cli.py employees list
python cli.py batch informativa_privacy_dipendenti consegna_policy --all --set policy_version=2.0 --send
python cli.py batch consegna_policy --emp IT001 --emp IT004 --send
python cli.py batch consegna_policy --all --regenerate-changed   # 默认跳过已生成；--force 全部重出
python cli.py employees export roster_status.xlsx                # 名册 + 状态列，粘回飞书
python cli.py inbox --days 30                 # IMAP 拉取签回件并归档
python cli.py archive <record_id> firmato.pdf # 手动归档
python cli.py matrix                          # 员工 × 文件状态
python cli.py register --fmt xlsx             # 备案登记表

# 单份
python cli.py list
python cli.py fields nda_mutual
python cli.py generate nda_mutual --data fill.json --to "Mario Rossi <mario@acme.it>" --cc legal@company.it --send
python cli.py send <record_id> --attach both
python cli.py history --status sent
```

## 4. 数据存放

| 内容 | 位置 |
|---|---|
| 模板 + 字段配置 | `templates_docx/*.docx` + `*.yaml`（纳入 git） |
| 生成的文件、.eml | `output/<record_id>/`（git 忽略） |
| 签回件归档 + 备案登记表 | `archive/`（git 忽略） |
| 员工名册 + 状态记录 | `data/records.db`（SQLite，git 忽略） |
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
│   ├── inbox.py           IMAP 拉取签回件，按 [DS-编号] 匹配
│   ├── archive.py         归档签回件、导出备案登记表
│   ├── employees.py       名册导入（CSV/XLSX，表头别名）
│   ├── store.py           SQLite：records + employees
│   └── service.py         生成、批量、发送、校验（网页和 CLI 共用）
├── web/                   HTML 页面
├── samples/               示例名册
└── templates_docx/        你的模板
```
