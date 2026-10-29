"""Agent performance report as a downloadable PDF (ReportLab: pure Python wheels, no system libraries).

    build_agent_report(agent, calls, ...) -> bytes

`calls` are the agent's call documents for the period (full documents where available, so follow-up
actions and speech measurements are included). Personal data is already redacted in them; redaction
placeholders such as [PHONE] are printed as they are.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, Line, String
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (CondPageBreak, Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

from callcenter import analytics

# Palette (matches the web app's light theme)
BRAND = colors.HexColor("#2563eb")
INK = colors.HexColor("#0f172a")
INK_2 = colors.HexColor("#475569")
MUTED = colors.HexColor("#94a3b8")
BORDER = colors.HexColor("#e2e8f0")
SOFT = colors.HexColor("#f1f5f9")
GOOD, GOOD_SOFT = colors.HexColor("#15803d"), colors.HexColor("#e3f6ea")
OK, OK_SOFT = colors.HexColor("#b45309"), colors.HexColor("#fdf1dc")
BAD, BAD_SOFT = colors.HexColor("#c62828"), colors.HexColor("#fde8e8")

PRIORITY_COLOR = {"High": BAD, "Medium": OK, "Low": GOOD}
RATING_COLOR = {"met": GOOD, "partial": OK, "not_met": BAD}
LOGO = Path(settings.BASE_DIR) / "static" / "img" / "ttbs-logo.png"
MAX_CALL_DETAILS = 60

# Built-in PDF fonts cover Latin-1 only: replace the few other symbols the analysis tends to use.
_REPLACE = {"→": "->", "←": "<-", "₹": "Rs ", "…": "...", "≥": ">=", "≤": "<=", "✓": "Yes", "✕": "No", "−": "-"}


def _clean(text) -> str:
    text = str(text if text is not None else "")
    for a, b in _REPLACE.items():
        text = text.replace(a, b)
    return text.encode("cp1252", "replace").decode("cp1252")


def _p(text, style) -> Paragraph:
    return Paragraph(escape(_clean(text)).replace("\n", "<br/>"), style)


def _pct(v) -> str:
    return "–" if v is None else f"{v:.0f}%"


def _dur(sec) -> str:
    return "–" if not sec else f"{int(sec) // 60}:{int(sec) % 60:02d}"


def _dt(value, fmt="%d %b %Y, %H:%M") -> str:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime(fmt)
    except (TypeError, ValueError):
        return str(value or "")


def _human(v) -> str:
    return str(v or "").replace("_", " ").capitalize()


def _score_color(v):
    if v is None:
        return INK_2
    return GOOD if v >= 85 else OK if v >= 70 else BAD


def _styles():
    base = getSampleStyleSheet()
    s = {
        "title": ParagraphStyle("title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=20, leading=24,
                                textColor=INK, alignment=TA_LEFT, spaceAfter=2),
        "subtitle": ParagraphStyle("subtitle", fontName="Helvetica", fontSize=10, leading=14, textColor=INK_2),
        "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=INK, spaceBefore=12,
                             spaceAfter=6),
        "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=INK),
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9, leading=12.5, textColor=INK),
        "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8, leading=10.5, textColor=INK_2),
        "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8, leading=10, textColor=INK),
        "cellb": ParagraphStyle("cellb", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=INK),
        "quote": ParagraphStyle("quote", fontName="Helvetica-Oblique", fontSize=8, leading=10.5, textColor=INK_2),
        "kpi_v": ParagraphStyle("kpi_v", fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=INK),
        "kpi_l": ParagraphStyle("kpi_l", fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=INK_2),
    }
    return s


def _table(rows, widths, header=True, zebra=True, extra=None):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), SOFT), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                  ("TEXTCOLOR", (0, 0), (-1, 0), INK_2), ("LINEBELOW", (0, 0), (-1, 0), 0.8, BORDER)]
    t.setStyle(TableStyle(style + (extra or [])))
    return t


def _kpis(agent, s, width):
    tiles = [
        ("Calls analysed", str(agent.get("calls", 0)), INK),
        ("Average QA", _pct(agent.get("avg_qa")), _score_color(agent.get("avg_qa"))),
        ("QA pass rate", _pct(agent.get("qa_pass_rate")), INK),
        ("Compliance", _pct(agent.get("compliance_rate")), _score_color(agent.get("compliance_rate"))),
        ("Resolved on call", _pct(agent.get("resolved_pct")), INK),
        ("Customer sentiment", "–" if agent.get("avg_sentiment") is None else f"{agent['avg_sentiment']:+.2f}", INK),
        ("Avg handle time", _dur(agent.get("avg_duration")), INK),
    ]
    cells = [[Paragraph(f'<font color="#{c.hexval()[2:]}">{escape(v)}</font>', s["kpi_v"]),
              Paragraph(escape(label), s["kpi_l"])] for label, v, c in tiles]
    t = Table([cells], colWidths=[width / len(tiles)] * len(tiles))
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, BORDER), ("INNERGRID", (0, 0), (-1, -1), 0.6, BORDER),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def _qa_chart(calls, threshold, width):
    pts = [(i + 1, c["qa"]["percent"]) for i, c in enumerate(calls) if (c.get("qa") or {}).get("percent") is not None]
    if len(pts) < 2:
        return None
    h = 52 * mm
    d = Drawing(width, h)
    lp = LinePlot()
    lp.x, lp.y, lp.width, lp.height = 28, 22, width - 40, h - 34
    lp.data = [pts]
    lp.lines[0].strokeColor = BRAND
    lp.lines[0].strokeWidth = 1.6
    lp.lines[0].symbol = makeMarker("FilledCircle", size=4, fillColor=BRAND, strokeColor=colors.white)
    lp.xValueAxis.valueMin, lp.xValueAxis.valueMax = 0.5, len(calls) + 0.5
    lp.xValueAxis.valueSteps = [p[0] for p in pts] if len(pts) <= 20 else None
    lp.xValueAxis.labels.fontSize = lp.yValueAxis.labels.fontSize = 7
    lp.xValueAxis.labels.fillColor = lp.yValueAxis.labels.fillColor = INK_2
    lp.xValueAxis.strokeColor = BORDER
    lp.yValueAxis.valueMin, lp.yValueAxis.valueMax, lp.yValueAxis.valueStep = 0, 100, 25
    lp.yValueAxis.strokeColor = colors.white
    lp.yValueAxis.visibleGrid, lp.yValueAxis.gridStrokeColor, lp.yValueAxis.gridStrokeWidth = 1, BORDER, 0.4
    d.add(lp)
    y = lp.y + lp.height * threshold / 100
    d.add(Line(lp.x, y, lp.x + lp.width, y, strokeColor=OK, strokeWidth=0.8, strokeDashArray=[3, 2]))
    d.add(String(lp.x + lp.width, y + 3, f"Pass mark {threshold:g}%", fontSize=7, fillColor=OK, textAnchor="end"))
    d.add(String(lp.x + lp.width / 2, 4, "Calls in date order (number = call in the list below)", fontSize=7,
                 fillColor=INK_2, textAnchor="middle"))
    return d


def _overall_assessment(agent, criteria, coaching, threshold, s):
    crit = [c for c in criteria if c.get("avg") is not None]
    strengths = sorted([c for c in crit if c["avg"] >= 80], key=lambda c: -c["avg"])[:4]
    gaps = sorted([c for c in crit if c["avg"] < 80], key=lambda c: c["avg"])[:4]
    qa = agent.get("avg_qa")
    if qa is None:
        verdict = "No scored calls in this period."
    elif qa >= threshold:
        verdict = f"Average QA of {qa:.0f}% is above the {threshold:g}% pass mark."
    else:
        verdict = f"Average QA of {qa:.0f}% is below the {threshold:g}% pass mark."
    out = [_p(verdict + f" {agent.get('qa_pass_rate') or 0:.0f}% of calls passed; "
              f"{agent.get('compliance_rate') or 0:.0f}% met every compliance check.", s["body"]), Spacer(1, 6)]
    left = [_p("Strengths", s["h2"])] + ([_p(f"• {c['name']} ({c['avg']:.0f}% met)", s["body"]) for c in strengths]
                                         or [_p("No parameter at 80% or more yet.", s["small"])])
    right = [_p("Focus areas", s["h2"])] + ([_p(f"• {c['name']} ({c['avg']:.0f}% met)", s["body"]) for c in gaps]
                                            or [_p("None: every parameter is at 80% or more.", s["small"])])
    t = Table([[left, right]], colWidths=["50%", "50%"])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("BACKGROUND", (0, 0), (0, 0), GOOD_SOFT), ("BACKGROUND", (1, 0), (1, 0), OK_SOFT),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    out.append(t)
    return out


def _coaching(coaching, s, width):
    if not coaching:
        return [_p("No coaching recommendations in this period.", s["small"])]
    rows = [["Priority", "Recommendation", "Calls"]]
    for r in coaching:
        style = ParagraphStyle("prio", parent=s["cellb"], textColor=PRIORITY_COLOR.get(r["priority"], INK))
        rows.append([_p(r["priority"], style),
                     [_p(r["title"], s["cellb"]), _p(r.get("detail", ""), s["cell"])],
                     _p(str(r["calls"]), s["cell"])])
    return [_table(rows, [18 * mm, width - 36 * mm, 18 * mm])]


def _criteria_table(criteria, team_avg, s, width):
    rows = [["Parameter", "Avg /5", "Fully", "Partial", "Not met", "N/A", "% met", "Team", "Verdict and recommendation"]]
    extra = []
    for i, c in enumerate(criteria, start=1):
        rec = [_p(c.get("verdict") or "–", s["cellb"])]
        if c.get("recommendation") and (c.get("avg") or 0) < 80:  # strengths need no "what to do"
            rec.append(_p(c["recommendation"], s["cell"]))
        rows.append([_p(c["name"], s["cell"]), "–" if c.get("avg_score") is None else f"{c['avg_score']:.1f}",
                     c["met"], c["partial"], c["not_met"], c["na"], _pct(c.get("avg")), _pct(team_avg.get(c["id"])),
                     rec])
        extra.append(("TEXTCOLOR", (6, i), (6, i), _score_color(c.get("avg"))))
        extra.append(("FONTNAME", (6, i), (6, i), "Helvetica-Bold"))
    num = 11 * mm
    widths = [44 * mm, num, num, num, num, 9 * mm, num, num]
    widths.append(width - sum(widths))
    extra += [("ALIGN", (1, 0), (7, -1), "CENTER")]
    return _table(rows, widths, extra=extra)


def _compliance_table(compliance, s, width):
    if not compliance:
        return _p("No compliance checks recorded.", s["small"])
    rows = [["Check", "Passed", "Failed", "N/A", "Pass rate"]]
    extra = []
    for i, c in enumerate(compliance, start=1):
        rows.append([_p(c["name"], s["cell"]), c["pass"], c["fail"], c["na"], _pct(c.get("pass_rate"))])
        if c["fail"]:
            extra.append(("TEXTCOLOR", (2, i), (2, i), BAD))
            extra.append(("FONTNAME", (2, i), (2, i), "Helvetica-Bold"))
    n = 18 * mm
    return _table(rows, [width - 4 * n, n, n, n, n], extra=extra + [("ALIGN", (1, 0), (-1, -1), "CENTER")])


def _call_list(calls, s, width):
    rows = [["#", "Date", "Call", "Reason", "Outcome", "Customer", "QA", "Flags"]]
    extra = []
    for i, c in enumerate(calls, start=1):
        qa = (c.get("qa") or {}).get("percent")
        flags = ", ".join(analytics.FLAG_LABELS.get(f, f) for f in c.get("flag_list") or [])
        rows.append([str(i), _dt(c.get("started_at"), "%d %b %H:%M"), _p(c.get("call_id"), s["cell"]),
                     _p(c.get("call_reason_name") or "–", s["cell"]), _human(c.get("resolution")),
                     _human((c.get("sentiment") or {}).get("customer")), _pct(qa), _p(flags or "–", s["cell"])])
        extra.append(("TEXTCOLOR", (6, i), (6, i), _score_color(qa)))
        extra.append(("FONTNAME", (6, i), (6, i), "Helvetica-Bold"))
    widths = [7 * mm, 22 * mm, 26 * mm, 36 * mm, 20 * mm, 17 * mm, 11 * mm]
    widths.append(width - sum(widths))
    return _table(rows, widths, extra=extra)


def _call_detail(n, c, s, width):
    qa = c.get("qa") or {}
    comp = c.get("compliance") or {}
    sent = c.get("sentiment") or {}
    emo = c.get("emotion") or {}
    head = (f"{n}. {c.get('call_reason_name') or 'Call'}  ·  {c.get('call_id')}")
    passed = "Pass" if qa.get("passed") else "Below threshold"
    meta = (f"{_dt(c.get('started_at'))}  ·  {_dur(c.get('duration_sec'))}  ·  {_human(c.get('direction')) or 'Call'}"
            f"  ·  Outcome: {_human(c.get('resolution'))}  ·  Customer: {_human(sent.get('customer'))}"
            f"  ·  Emotion: {emo.get('start') or '–'} -> {emo.get('end') or '–'}")
    qa_cell = Paragraph(f'<font color="#{_score_color(qa.get("percent")).hexval()[2:]}"><b>{_pct(qa.get("percent"))}</b></font>'
                        f'<br/><font size="7" color="#475569">{passed}</font>', s["cell"])
    header = Table([[[_p(head, s["h2"]), _p(meta, s["small"])], qa_cell]], colWidths=[width - 26 * mm, 26 * mm])
    header.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                ("ALIGN", (1, 0), (1, 0), "CENTER"), ("TOPPADDING", (0, 0), (-1, -1), 6),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 7)]))
    block = [header, Spacer(1, 4)]
    if c.get("flag_list"):
        flags = ", ".join(analytics.FLAG_LABELS.get(f, f) for f in c["flag_list"])
        block.append(Paragraph(f'<font color="#c62828"><b>Flags:</b> {escape(_clean(flags))}</font>', s["small"]))
    block.append(_p(c.get("summary") or "", s["body"]))
    if c.get("root_cause"):
        block.append(Paragraph(f"<b>Root cause:</b> {escape(_clean(c['root_cause']))}", s["small"]))

    failed = [x for x in comp.get("checks", []) if x.get("status") == "fail"]
    if failed:
        block += [Spacer(1, 4), _p("Compliance failures", s["h2"])]
        for x in failed:
            block.append(Paragraph(f'<font color="#c62828"><b>Failed:</b></font> <b>{escape(_clean(x["name"]))}</b>',
                                   s["cell"]))
            if x.get("evidence"):
                block.append(_p(f'"{x["evidence"]}"', s["quote"]))

    weak = [q for q in qa.get("criteria", []) if q.get("rating") in ("not_met", "partial")]
    if weak:
        block += [Spacer(1, 4), _p("What to improve on this call", s["h2"])]
        rows = [["Parameter", "Rating", "Why, and what to do"]]
        extra = []
        for i, q in enumerate(sorted(weak, key=lambda q: q.get("rating") != "not_met"), start=1):
            why = [_p(q.get("rationale") or "", s["cell"])]
            if q.get("evidence"):
                why.append(_p(f'"{q["evidence"]}"', s["quote"]))
            if q.get("recommendation"):
                why.append(Paragraph(f"<b>Do:</b> {escape(_clean(q['recommendation']))}", s["cell"]))
            rows.append([_p(q.get("name"), s["cell"]), q.get("rating_label") or _human(q.get("rating")), why])
            extra.append(("TEXTCOLOR", (1, i), (1, i), RATING_COLOR.get(q.get("rating"), INK)))
            extra.append(("FONTNAME", (1, i), (1, i), "Helvetica-Bold"))
        block.append(_table(rows, [45 * mm, 18 * mm, width - 63 * mm], extra=extra))
    strong = [q.get("name") for q in qa.get("criteria", []) if q.get("rating") == "met"]
    if strong:
        block += [Spacer(1, 3), Paragraph(f'<font color="#15803d"><b>Done well:</b></font> '
                                          f'{escape(_clean(", ".join(strong)))}', s["small"])]

    if c.get("coaching"):
        block += [Spacer(1, 4), _p("Coaching", s["h2"])]
        for r in c["coaching"]:
            color = PRIORITY_COLOR.get(r.get("priority"), INK).hexval()[2:]
            block.append(Paragraph(f'<font color="#{color}"><b>[{escape(r.get("priority", ""))}]</b></font> '
                                   f'<b>{escape(_clean(r.get("title", "")))}</b>: {escape(_clean(r.get("detail", "")))}',
                                   s["cell"]))
    if c.get("follow_up_actions"):
        block += [Spacer(1, 3), Paragraph("<b>Follow-up:</b> " + escape(_clean("; ".join(c["follow_up_actions"]))),
                                          s["small"])]
    m = c.get("speech_metrics") or {}
    if m:
        block += [Spacer(1, 3), _p(
            f"Measured: pace {m.get('agent_wpm') or '–'} words/min · agent talk time {m.get('agent_talk_pct') or '–'}% · "
            f"{m.get('dead_air_count', 0)} silence(s) of 10 s+ · {m.get('overlaps', 0)} talk-over(s) "
            f"({m.get('agent_interruptions', 0)} by the agent)", s["small"])]
    block.append(Spacer(1, 10))
    return block


def build_agent_report(agent: dict, calls: list[dict], *, period: str, team_avg: dict, threshold: float,
                       generated_by: str, org: str = "Tata Tele Business Services") -> bytes:
    s = _styles()
    buf = io.BytesIO()
    margin = 15 * mm
    width = A4[0] - 2 * margin
    generated = datetime.now().strftime("%d %b %Y, %H:%M")
    name = agent.get("agent_name") or agent.get("agent_id") or "Agent"

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(margin, 9 * mm, _clean(f"{name} · Agent performance report · {period}"))
        canvas.drawRightString(A4[0] - margin, 9 * mm, f"Page {doc.page}")
        canvas.drawCentredString(A4[0] / 2, 9 * mm, "Confidential")
        canvas.setStrokeColor(BORDER)
        canvas.line(margin, 12 * mm, A4[0] - margin, 12 * mm)
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=margin, rightMargin=margin, topMargin=margin,
                            bottomMargin=18 * mm, title=f"Agent performance report – {name}", author=org,
                            subject=f"Call Center Analysis – {period}")
    calls = sorted(calls, key=lambda c: c.get("started_at") or "")
    criteria = analytics.criteria_averages(calls)
    coaching = analytics.coaching_summary(calls, limit=12)
    compliance = analytics.compliance_summary(calls)

    story = []
    logo = Image(str(LOGO), width=38 * mm, height=38 * mm * 0.32, kind="proportional") if LOGO.is_file() else ""
    title = [_p("Agent performance report", s["title"]),
             _p(f"{name}  ·  {agent.get('team') or 'No team'}  ·  Agent ID {agent.get('agent_id') or '–'}", s["subtitle"]),
             _p(f"Period: {period}  ·  Generated {generated} by {generated_by}", s["small"])]
    head = Table([[title, logo]], colWidths=[width - 42 * mm, 42 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                              ("LINEBELOW", (0, 0), (-1, 0), 1.2, BRAND), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story += [head, Spacer(1, 10), _kpis(agent, s, width)]

    if not calls:
        story += [Spacer(1, 12), _p("No analysed calls in this period.", s["body"])]
        doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
        return buf.getvalue()

    story += [_p("Overall assessment", s["h1"]), *_overall_assessment(agent, criteria, coaching, threshold, s)]
    chart = _qa_chart(calls, threshold, width)
    if chart:
        story += [_p("QA score per call", s["h1"]), chart]
    story += [CondPageBreak(60 * mm), _p("What to improve: coaching recommendations", s["h1"]),
              _p("Combined across the period's calls, highest priority first.", s["small"]), Spacer(1, 4),
              *_coaching(coaching, s, width)]
    story += [CondPageBreak(90 * mm), _p(f"{len(criteria)}-parameter QA evaluation", s["h1"]),
              _p("Fully met = 5, Partial = 3, Not met = 0; N/A is left out. % met counts Partial as half. "
                 "Team = the same figure for all agents in the period.", s["small"]), Spacer(1, 4),
              _criteria_table(criteria, team_avg, s, width)]
    story += [CondPageBreak(50 * mm), _p("Compliance checks", s["h1"]), _compliance_table(compliance, s, width)]
    story += [CondPageBreak(60 * mm), _p(f"Calls in this period ({len(calls)})", s["h1"]), _call_list(calls, s, width)]

    story += [PageBreak(), _p("Call-by-call review", s["h1"]),
              _p("For each call: what happened, what to improve (Not met and Partial ratings with the reason and a "
                 "recommendation), compliance failures and coaching.", s["small"]), Spacer(1, 6)]
    shown = calls[-MAX_CALL_DETAILS:]
    if len(calls) > len(shown):
        story.append(_p(f"Showing the {len(shown)} most recent calls of {len(calls)}.", s["small"]))
    offset = len(calls) - len(shown)
    for i, c in enumerate(shown, start=offset + 1):
        block = _call_detail(i, c, s, width)
        story += [KeepTogether(block[:4]), *block[4:]]
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()
