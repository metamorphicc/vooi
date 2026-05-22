from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


OUT = Path("Funding_Regime_Rotation_Test_Report.docx")


def main() -> None:
    doc = Document()
    setup_document(doc)
    setup_styles(doc)

    add_title(doc)
    add_executive_summary(doc)
    add_key_metrics(doc)
    add_closed_positions(doc)
    add_return_calculation(doc)
    add_trade_review(doc)
    add_findings(doc)
    add_next_steps(doc)

    doc.save(OUT)
    print(OUT.resolve())


def setup_document(doc: Document) -> None:
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(1.0)
    section.bottom_margin = Inches(1.0)
    section.left_margin = Inches(1.0)
    section.right_margin = Inches(1.0)
    section.header_distance = Inches(0.492)
    section.footer_distance = Inches(0.492)


def setup_styles(doc: Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
    normal.font.size = Pt(11)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.10

    title = doc.styles["Title"]
    title.font.name = "Calibri"
    title.font.size = Pt(24)
    title.font.bold = True
    title.font.color.rgb = RGBColor(11, 37, 69)
    title.paragraph_format.space_after = Pt(10)

    subtitle = doc.styles["Subtitle"]
    subtitle.font.name = "Calibri"
    subtitle.font.size = Pt(11)
    subtitle.font.color.rgb = RGBColor(85, 85, 85)
    subtitle.paragraph_format.space_after = Pt(14)

    for name, size, color, before, after in [
        ("Heading 1", 16, RGBColor(46, 116, 181), 16, 8),
        ("Heading 2", 13, RGBColor(46, 116, 181), 12, 6),
        ("Heading 3", 12, RGBColor(31, 77, 120), 8, 4),
    ]:
        style = doc.styles[name]
        style.font.name = "Calibri"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = color
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)


def add_title(doc: Document) -> None:
    doc.add_paragraph("Funding Regime Rotation Bot: Live Test Report", style="Title")
    doc.add_paragraph(
        "VOOI MCP live perps test | Hyperliquid and Lighter | Report date: 15 May 2026",
        style="Subtitle",
    )
    add_note(
        doc,
        "Test status",
        "The bot completed 4 closed live positions during this reporting window. "
        "The strategy showed positive net PnL, with most of the realized result coming from funding.",
    )


def add_executive_summary(doc: Document) -> None:
    doc.add_heading("Executive Summary", level=1)
    add_body(
        doc,
        "The live test was run with a small account and a quote size of 40 USDC per strategy position. "
        "The bot selected cross-venue funding regimes, opened hedged long/short perps positions, and rotated "
        "when the current regime deteriorated or a materially better candidate appeared.",
    )
    add_body(
        doc,
        "Across the 4 closed positions, the strategy produced +0.059237 USDC net PnL. Funding PnL was "
        "+0.057067 USDC, while price PnL was +0.002170 USDC. This is a useful early signal because the "
        "positive result came primarily from the intended source: funding differential capture.",
    )


def add_key_metrics(doc: Document) -> None:
    doc.add_heading("Key Metrics", level=1)
    rows = [
        ("Closed live positions", "4"),
        ("Total hold time", "17.19 hours"),
        ("Total quote size across closed positions", "160 USDC"),
        ("Entry notional across closed positions", "318.696139 USDC"),
        ("Net PnL", "+0.059237 USDC"),
        ("Funding PnL", "+0.057067 USDC"),
        ("Price PnL", "+0.002170 USDC"),
        ("Return on quote size", "0.0370%"),
        ("Return on entry notional", "0.0186%"),
        ("Annualized run-rate on quote size", "~18.9% APR"),
    ]
    table = doc.add_table(rows=1, cols=2)
    style_table(table)
    hdr = table.rows[0].cells
    hdr[0].text = "Metric"
    hdr[1].text = "Value"
    shade_row(table.rows[0], "F2F4F7")
    for metric, value in rows:
        row = table.add_row().cells
        row[0].text = metric
        row[1].text = value
    set_table_widths(table, [4700, 4300])


def add_closed_positions(doc: Document) -> None:
    doc.add_heading("Closed Position Log", level=1)
    table = doc.add_table(rows=1, cols=6)
    style_table(table)
    headers = ["ID", "Asset", "Route", "Hold", "Net PnL", "Funding PnL"]
    for idx, header in enumerate(headers):
        table.rows[0].cells[idx].text = header
    shade_row(table.rows[0], "F2F4F7")

    rows = [
        ("#6", "CHIP", "Hyperliquid -> Lighter", "4.24h", "-0.028273", "+0.017919"),
        ("#7", "HYPE", "Lighter -> Hyperliquid", "4.16h", "+0.066196", "+0.000556"),
        ("#8", "CHIP", "Hyperliquid -> Lighter", "4.77h", "+0.022908", "+0.030186"),
        ("#9", "XMR", "Lighter -> Hyperliquid", "4.02h", "-0.001594", "+0.008406"),
    ]
    for values in rows:
        cells = table.add_row().cells
        for idx, value in enumerate(values):
            cells[idx].text = value
    set_table_widths(table, [650, 900, 2900, 900, 1600, 1600])


def add_return_calculation(doc: Document) -> None:
    doc.add_heading("Return Calculation", level=1)
    add_body(
        doc,
        "The closed-position sample generated 0.059237 USDC on 160 USDC of cumulative quote size. "
        "That equals approximately 0.0370% over 17.19 position-hours.",
    )
    add_note(
        doc,
        "Annualized estimate",
        "0.0370% * 24 / 17.19 * 365 = approximately 18.9% APR. This is a rough run-rate, not a stable "
        "forward return estimate. The sample is still small and should be treated as an early signal.",
    )

    table = doc.add_table(rows=1, cols=4)
    style_table(table)
    headers = ["Quote size", "Estimated net PnL", "Estimated funding PnL", "Estimated price PnL"]
    for idx, header in enumerate(headers):
        table.rows[0].cells[idx].text = header
    shade_row(table.rows[0], "F2F4F7")
    rows = [
        ("500 USDC", "+0.185116 USDC", "+0.178334 USDC", "+0.006781 USDC"),
        ("1000 USDC", "+0.370231 USDC", "+0.356669 USDC", "+0.013562 USDC"),
    ]
    for values in rows:
        cells = table.add_row().cells
        for idx, value in enumerate(values):
            cells[idx].text = value
    set_table_widths(table, [1800, 2500, 2500, 2500])


def add_trade_review(doc: Document) -> None:
    doc.add_heading("Trade-Level Review", level=1)
    bullets = [
        "CHIP #6 earned positive funding but closed negative overall because price/basis movement outweighed the funding income.",
        "HYPE #7 delivered the largest net gain, but most of that gain came from price PnL rather than funding. It is a useful positive result but should not be treated as the core edge.",
        "CHIP #8 was the cleanest example of the strategy working as intended: funding was positive and large enough to offset a small negative price component.",
        "XMR #9 was close to flat: funding was positive, but price/basis movement absorbed most of the edge.",
    ]
    for item in bullets:
        p = doc.add_paragraph(style="List Bullet")
        p.add_run(item)


def add_findings(doc: Document) -> None:
    doc.add_heading("Interpretation", level=1)
    add_body(
        doc,
        "The main conclusion is that the bot is not merely producing random rotation output: funding is being "
        "captured and recorded. The total funding PnL is positive across all closed positions, and it accounts "
        "for nearly all of the total net PnL in this test window.",
    )
    add_body(
        doc,
        "The primary risk visible in this sample is price leakage: even when the strategy is hedged, differences "
        "between venues, spread, execution, fees, and basis movement can reduce or fully offset funding income. "
        "This makes rotation discipline important. If the bot rotates too frequently, the funding edge may not "
        "have enough time to overcome entry and exit friction.",
    )


def add_next_steps(doc: Document) -> None:
    doc.add_heading("Recommended Next Steps", level=1)
    steps = [
        "Continue running the bot for at least 2-3 full days to collect a larger sample of closed positions.",
        "Track funding PnL and price PnL separately after every exit.",
        "Review which assets repeatedly lose to price/basis movement and consider excluding or penalizing them.",
        "Keep quote size conservative until the sample includes at least 10-20 closed positions.",
        "Evaluate annualized return only after a larger sample, because 4 closed positions are not enough to prove stable APR.",
    ]
    for item in steps:
        p = doc.add_paragraph(style="List Number")
        p.add_run(item)


def add_body(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.add_run(text)


def add_note(doc: Document, label: str, text: str) -> None:
    table = doc.add_table(rows=1, cols=1)
    style_table(table, border_color="DADCE0")
    cell = table.rows[0].cells[0]
    cell.text = ""
    shade_cell(cell, "F4F6F9")
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(f"{label}: ")
    run.bold = True
    p.add_run(text)
    set_table_widths(table, [9360])


def style_table(table, border_color: str = "B7C3D0") -> None:
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:type"), "dxa")
    tbl_w.set(qn("w:w"), "9360")
    borders = OxmlElement("w:tblBorders")
    for border_name in ["top", "left", "bottom", "right", "insideH", "insideV"]:
        border = OxmlElement(f"w:{border_name}")
        border.set(qn("w:val"), "single")
        border.set(qn("w:sz"), "4")
        border.set(qn("w:space"), "0")
        border.set(qn("w:color"), border_color)
        borders.append(border)
    tbl_pr.append(borders)
    for row in table.rows:
        for cell in row.cells:
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after = Pt(4)


def shade_row(row, fill: str) -> None:
    for cell in row.cells:
        shade_cell(cell, fill)
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.bold = True


def shade_cell(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def set_table_widths(table, widths: list[int]) -> None:
    grid = table._tbl.tblGrid
    if grid is None:
        grid = OxmlElement("w:tblGrid")
        table._tbl.insert(0, grid)
    for child in list(grid):
        grid.remove(child)
    for width in widths:
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(width))
        grid.append(col)
    for row in table.rows:
        for idx, width in enumerate(widths):
            cell = row.cells[idx]
            cell.width = Pt(width / 20)
            tc_pr = cell._tc.get_or_add_tcPr()
            tc_w = tc_pr.find(qn("w:tcW"))
            if tc_w is None:
                tc_w = OxmlElement("w:tcW")
                tc_pr.append(tc_w)
            tc_w.set(qn("w:w"), str(width))
            tc_w.set(qn("w:type"), "dxa")


if __name__ == "__main__":
    main()
