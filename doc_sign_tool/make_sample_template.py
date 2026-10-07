"""Create templates_docx/nda_mutual.docx (a bilingual EN/IT NDA skeleton) to demo the tool."""
from pathlib import Path

from docx import Document
from docx.shared import Pt

out = Path(__file__).parent / "templates_docx" / "nda_mutual.docx"
doc = Document()
style = doc.styles["Normal"]; style.font.name = "Calibri"; style.font.size = Pt(11)

doc.add_heading("MUTUAL NON-DISCLOSURE AGREEMENT / ACCORDO DI RISERVATEZZA", level=1)
doc.add_paragraph("Between / Tra")
doc.add_paragraph("{{ company_name }}, with registered office at {{ company_address }}, VAT no. {{ company_vat }}, "
                  "represented by {{ company_signatory }} (\"Company\")")
doc.add_paragraph("and / e")
doc.add_paragraph("{{ counterparty_name }}, with registered office at {{ counterparty_address }}, VAT no. {{ counterparty_vat }}, "
                  "represented by {{ counterparty_signatory }} (\"Counterparty\")")
doc.add_paragraph("Effective date / Data di efficacia: {{ effective_date | date }}")
doc.add_heading("1. Purpose / Finalità", level=2)
doc.add_paragraph("The Parties intend to exchange confidential information in connection with: {{ purpose }}.")
doc.add_heading("2. Term / Durata", level=2)
doc.add_paragraph("This Agreement remains in force for {{ term_years }} year(s) from the Effective Date. "
                  "Confidentiality obligations survive for {{ survival_years }} year(s) after termination.")
doc.add_heading("3. Governing law / Legge applicabile", level=2)
doc.add_paragraph("This Agreement is governed by the laws of {{ governing_law }}. Exclusive jurisdiction: Court of {{ jurisdiction }}.")
doc.add_paragraph("{% if penalty_amount %}4. Penalty / Penale: in case of breach, the defaulting Party shall pay "
                  "{{ penalty_amount | money }}, without prejudice to further damages.{% endif %}")
doc.add_paragraph("")
t = doc.add_table(rows=3, cols=2)
t.cell(0, 0).text = "{{ company_name }}"; t.cell(0, 1).text = "{{ counterparty_name }}"
t.cell(1, 0).text = "{{ company_signatory }}"; t.cell(1, 1).text = "{{ counterparty_signatory }}"
t.cell(2, 0).text = "Signature: ______________________"; t.cell(2, 1).text = "Signature: ______________________"
doc.add_paragraph("")
doc.add_paragraph("Generated on {{ today_it }}").runs[0].font.size = Pt(8)
doc.save(out)
print(out)
