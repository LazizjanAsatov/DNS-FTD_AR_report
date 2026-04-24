"""Read CSV, filter FTD transactions, compute per-merchant metrics, write PDF."""
import io
import logging
from urllib import request

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

log = logging.getLogger(__name__)

MERCHANT_COL = "Merchant"
PROCESSOR_COL = "Processor"
GATE_COL = "Gate Name"
STATUS_COL = "Status"
AMOUNT_COL = "Amount in currency for processing"
CURRENCY_COL = "Currency for processing"
DATE_COL = "Created Date (Server TZ, no time)"

RATES_URL = "https://docs.google.com/spreadsheets/d/1kd1YLo4Loo0sYpCM4exwbplipkmsJXuRnqpU8mdQeNU/export?format=csv&gid=0"


def _load_rates():
    with request.urlopen(RATES_URL, timeout=20) as r:
        raw = r.read().decode("utf-8")
    rates = pd.read_csv(io.StringIO(raw))
    rates["Currency"] = rates["Currency"].str.strip().str.upper()
    rates["Rate"] = pd.to_numeric(rates["Rate"], errors="coerce")
    return dict(zip(rates["Currency"], rates["Rate"]))


def _ftd_masks(df):
    proc = df[PROCESSOR_COL].fillna("").str.lower()
    gate = df[GATE_COL].fillna("").str.lower()
    return {
        "ftd": proc.str.contains("ftd"),
        "prime": gate.str.contains("ftdprime"),
        "instance": gate.str.contains("ftdinstance"),
    }


def build_report(csv_path, pdf_path, days=7):
    log.info("Reading %s (window=%d days)", csv_path, days)
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)

    log.info("Loading rates")
    rates = _load_rates()

    df[AMOUNT_COL] = pd.to_numeric(df[AMOUNT_COL], errors="coerce").fillna(0)
    df[CURRENCY_COL] = df[CURRENCY_COL].str.strip().str.upper()
    df["_rate"] = df[CURRENCY_COL].map(rates)
    missing = df[df["_rate"].isna()][CURRENCY_COL].unique().tolist()
    if missing:
        log.warning("Currencies missing from rate table: %s", missing)
    df["_rate"] = df["_rate"].fillna(0)
    df["_usd"] = df[AMOUNT_COL] * df["_rate"]
    df["_approved"] = df[STATUS_COL].str.lower().eq("approved")

    dates = pd.to_datetime(df[DATE_COL], errors="coerce").dt.date
    max_date = dates.max()
    cutoff = max_date - pd.Timedelta(days=days - 1).to_pytimedelta()
    df = df[dates >= cutoff].copy()
    log.info("Filtered to %s → %s (%d rows)", cutoff, max_date, len(df))

    masks = _ftd_masks(df)
    any_ftd = masks["ftd"] | masks["prime"] | masks["instance"]
    ftd = df[any_ftd].copy()
    log.info("FTD rows: %d / %d", len(ftd), len(df))

    per_merchant = ftd.groupby(MERCHANT_COL).agg(
        volume=("_usd", lambda s: s[ftd.loc[s.index, "_approved"]].sum()),
        overall_approved=("_approved", "sum"),
        overall_total=("_approved", "size"),
    )

    ftd_only = ftd[masks["ftd"].loc[ftd.index]]
    ftd_ar = ftd_only.groupby(MERCHANT_COL).agg(
        ftd_approved=("_approved", "sum"),
        ftd_total=("_approved", "size"),
    )
    ftd_vol = ftd_only[ftd_only["_approved"]].groupby(MERCHANT_COL)["_usd"].sum().rename("ftd_vol")

    prime_vol = (
        ftd[masks["prime"].loc[ftd.index] & ftd["_approved"]]
        .groupby(MERCHANT_COL)["_usd"].sum().rename("prime_vol")
    )
    instance_vol = (
        ftd[masks["instance"].loc[ftd.index] & ftd["_approved"]]
        .groupby(MERCHANT_COL)["_usd"].sum().rename("instance_vol")
    )

    out = per_merchant.join([ftd_ar, ftd_vol, prime_vol, instance_vol], how="left").fillna(0)
    out = out[out["instance_vol"] >= 1].sort_values("volume", ascending=False).reset_index()

    def _ar(a, t):
        return f"{a / t:.0%}" if t else ""

    rows = []
    for _, r in out.iterrows():
        rows.append({
            "Merchant": r[MERCHANT_COL],
            "Volume": r["volume"],
            "Overall AR": _ar(r["overall_approved"], r["overall_total"]),
            "FTD AR": _ar(r["ftd_approved"], r["ftd_total"]),
            "FTD Vol": r["ftd_vol"],
            "FTD Prime Vol": r["prime_vol"],
            "FTD Instance Vol": r["instance_vol"],
        })
    table_df = pd.DataFrame(rows)

    totals = {
        "Merchant": "TOTAL",
        "Volume": out["volume"].sum(),
        "Overall AR": _ar(out["overall_approved"].sum(), out["overall_total"].sum()),
        "FTD AR": _ar(out["ftd_approved"].sum(), out["ftd_total"].sum()),
        "FTD Vol": out["ftd_vol"].sum(),
        "FTD Prime Vol": out["prime_vol"].sum(),
        "FTD Instance Vol": out["instance_vol"].sum(),
    }

    _write_pdf(table_df, totals, pdf_path, days=days)
    log.info("Wrote PDF")
    return pdf_path


def _fmt_money(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    if v == 0:
        return "—"
    return f"${v:,.0f}"


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


def _write_pdf(table_df, totals, pdf_path, days=7):
    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=landscape(A4),
        leftMargin=24, rightMargin=24, topMargin=28, bottomMargin=24,
        title=f"FTD Report ({days}d)",
    )
    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    title_style.fontSize = 16
    title_style.textColor = colors.HexColor("#1F3864")

    story = [
        Paragraph(f"FTD Report — last {days} days", title_style),
        Paragraph(
            "<font size=10 color='#5A5A5A'>Volumes in USD (approved only)</font>",
            styles["Normal"],
        ),
        Spacer(1, 14),
    ]

    headers = list(table_df.columns)
    body = []
    for _, r in table_df.iterrows():
        body.append([
            r["Merchant"],
            _fmt_money(r["Volume"]),
            r["Overall AR"],
            r["FTD AR"],
            _fmt_money(r["FTD Vol"]),
            _fmt_money(r["FTD Prime Vol"]),
            _fmt_money(r["FTD Instance Vol"]),
        ])
    footer = [
        totals["Merchant"],
        _fmt_money(totals["Volume"]),
        totals["Overall AR"],
        totals["FTD AR"],
        _fmt_money(totals["FTD Vol"]),
        _fmt_money(totals["FTD Prime Vol"]),
        _fmt_money(totals["FTD Instance Vol"]),
    ]
    data = [headers] + body + [footer]

    page_w = landscape(A4)[0] - 48
    merchant_w = min(200, page_w * 0.26)
    other_w = (page_w - merchant_w) / (len(headers) - 1)
    col_widths = [merchant_w] + [other_w] * (len(headers) - 1)

    tbl = Table(data, repeatRows=1, colWidths=col_widths)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3864")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("FONTNAME", (0, 1), (0, -2), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 1), (0, -2), colors.HexColor("#1F3864")),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("ALIGN", (0, 1), (0, -1), "LEFT"),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("ALIGN", (2, 1), (3, -1), "CENTER"),
        ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B4B4B4")),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, colors.HexColor("#1F3864")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#F4F6FA")]),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#1F3864")),
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 1.2, colors.HexColor("#1F3864")),
    ]

    for r_idx, row in enumerate(body, start=1):
        for c_idx in (2, 3):
            bg = _ar_color(row[c_idx])
            if bg:
                style.append(("BACKGROUND", (c_idx, r_idx), (c_idx, r_idx), bg))

    tbl.setStyle(TableStyle(style))
    story.append(tbl)
    doc.build(story)


if __name__ == "__main__":
    from pathlib import Path
    base = Path(__file__).resolve().parent
    build_report(str(base / "input.csv"), str(base / "FTD_report.pdf"))
