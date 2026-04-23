"""Read CSV, filter FTD transactions, pivot merchant x date, write PDF table."""
import logging

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

log = logging.getLogger(__name__)

DATE_COL = "Created Date (Server TZ, no time)"
MERCHANT_COL = "Merchant"
PROCESSOR_COL = "Processor"
GATE_COL = "Gate Name"
STATUS_COL = "Status"


def _is_ftd(row):
    proc = str(row.get(PROCESSOR_COL, "")).lower()
    gate = str(row.get(GATE_COL, "")).lower()
    return "ftd" in proc or "ftdprime" in gate or "ftdinstance" in gate


def build_report(csv_path, xlsx_path, pdf_path):
    log.info("Reading %s", csv_path)
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)

    mask = df.apply(_is_ftd, axis=1)
    ftd = df[mask].copy()
    log.info("FTD rows: %d / %d total", len(ftd), len(df))

    if ftd.empty:
        pivot = pd.DataFrame(columns=[MERCHANT_COL])
    else:
        ftd[DATE_COL] = pd.to_datetime(ftd[DATE_COL], errors="coerce").dt.date
        ftd["_approved"] = ftd[STATUS_COL].str.lower().eq("approved").astype(int)

        grouped = ftd.groupby([MERCHANT_COL, DATE_COL]).agg(
            approved=("_approved", "sum"),
            total=("_approved", "size"),
        )
        grouped["ar"] = grouped.apply(
            lambda r: f"{r['approved']}/{r['total']} ({r['approved'] / r['total']:.0%})" if r["total"] else "",
            axis=1,
        )
        pivot = grouped["ar"].unstack(fill_value="").sort_index(axis=1)
        totals = ftd.groupby(MERCHANT_COL).size()
        pivot = pivot.loc[totals.sort_values(ascending=False).index].reset_index()

    pivot.to_excel(xlsx_path, index=False)
    log.info("Wrote %s", xlsx_path)

    _write_pdf(pivot, pdf_path)
    log.info("Wrote %s", pdf_path)
    return xlsx_path, pdf_path


def _ar_color(cell):
    if not cell:
        return None
    import re
    m = re.search(r"(\d+)%", cell)
    if not m:
        return None
    pct = int(m.group(1))
    if pct > 50:
        return colors.HexColor("#C6EFCE")
    if pct >= 31:
        return colors.HexColor("#FFEB9C")
    return colors.HexColor("#FFC7CE")


def _write_pdf(pivot, pdf_path):
    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=landscape(A4),
        leftMargin=24,
        rightMargin=24,
        topMargin=28,
        bottomMargin=24,
        title="FTD Report",
    )
    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    title_style.fontSize = 16
    title_style.textColor = colors.HexColor("#1F3864")

    date_cols = [c for c in pivot.columns if c != MERCHANT_COL]
    date_range = ""
    if date_cols:
        first = date_cols[0].strftime("%d %b") if hasattr(date_cols[0], "strftime") else str(date_cols[0])
        last = date_cols[-1].strftime("%d %b %Y") if hasattr(date_cols[-1], "strftime") else str(date_cols[-1])
        date_range = f"{first} — {last}"

    story = [
        Paragraph("FTD Report", title_style),
        Paragraph(
            f"<font size=10 color='#5A5A5A'>{date_range} &nbsp;·&nbsp; cells show approved/total (AR%)</font>",
            styles["Normal"],
        ),
        Spacer(1, 14),
    ]

    headers = [
        c.strftime("%a\n%d %b") if hasattr(c, "strftime") else str(c)
        for c in pivot.columns
    ]
    data = [headers] + pivot.astype(str).values.tolist()

    page_w = landscape(A4)[0] - 48
    merchant_w = min(200, page_w * 0.25)
    date_w = (page_w - merchant_w) / max(len(date_cols), 1) if date_cols else page_w
    col_widths = [merchant_w] + [date_w] * len(date_cols)

    tbl = Table(data, repeatRows=1, colWidths=col_widths)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3864")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 1), (-1, -1), 7.5),
        ("TEXTCOLOR", (0, 1), (0, -1), colors.HexColor("#1F3864")),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ("ALIGN", (0, 1), (0, -1), "LEFT"),
        ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B4B4B4")),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, colors.HexColor("#1F3864")),
        ("ROWBACKGROUNDS", (0, 1), (0, -1), [colors.white, colors.HexColor("#F4F6FA")]),
    ]

    for r, row in enumerate(data[1:], start=1):
        for c, cell in enumerate(row[1:], start=1):
            bg = _ar_color(cell)
            if bg:
                style.append(("BACKGROUND", (c, r), (c, r), bg))

    tbl.setStyle(TableStyle(style))
    story.append(tbl)
    doc.build(story)


if __name__ == "__main__":
    from datetime import date, timedelta
    from pathlib import Path
    base = Path(__file__).resolve().parent
    stamp = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    build_report(
        str(base / "input.csv"),
        str(base / f"FTD_report_{stamp}.xlsx"),
        str(base / f"FTD_report_{stamp}.pdf"),
    )
