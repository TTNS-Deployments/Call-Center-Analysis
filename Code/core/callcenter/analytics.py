"""Filters and aggregations for the web app dashboards.

These run over call summaries in memory, which is fine for operational views (days to a
few weeks of calls). Long-range and heavy analytics belong in Power BI over the Parquet data.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean

FLAG_LABELS = {
    "unhappy_customer": "Unhappy customer",
    "repeat_contact": "Repeat contact",
    "unresolved": "Unresolved",
    "compliance_breach": "Compliance breach",
    "low_qa": "Low QA score",
}
FLAG_WEIGHT = {"compliance_breach": 4, "unhappy_customer": 3, "repeat_contact": 3, "unresolved": 2, "low_qa": 1}


def _completed(items):
    return [c for c in items if c.get("status") == "completed"]


def _qa(c) -> float | None:
    """Call QA % (None when every parameter was N/A or the call has no QA yet)."""
    return (c.get("qa") or {}).get("percent")


def _mean_qa(calls) -> float | None:
    vals = [_qa(c) for c in calls if _qa(c) is not None]
    return _r(mean(vals)) if vals else None


def _verdicts() -> list[dict]:
    from .rules import DEFAULT_VERDICTS

    try:
        from .rules import load_rules

        return load_rules().rubric.get("verdicts") or DEFAULT_VERDICTS
    except Exception:  # noqa: BLE001 - config unavailable: use defaults
        return DEFAULT_VERDICTS


def verdict(percent: float | None, verdicts: list[dict] | None = None) -> str:
    if percent is None:
        return "Not applicable"
    for v in sorted(verdicts or _verdicts(), key=lambda v: -float(v["min"])):
        if percent >= float(v["min"]):
            return v["label"]
    return ""


def _r(value, digits=1):
    return round(value, digits) if value is not None else None


def _pct(part, whole):
    return round(100 * part / whole, 1) if whole else None


def filter_calls(items: list[dict], f: dict) -> list[dict]:
    q = (f.get("q") or "").strip().lower()
    out = []
    for c in items:
        if q and not any(q in str(c.get(k) or "").lower()
                         for k in ("call_id", "agent_name", "summary", "call_reason_name", "root_cause", "customer_id")):
            continue
        if f.get("agent") and c.get("agent_id") != f["agent"]:
            continue
        if f.get("team") and c.get("team") != f["team"]:
            continue
        if f.get("reason") and c.get("call_reason") != f["reason"]:
            continue
        if f.get("sentiment") and (c.get("sentiment") or {}).get("customer") != f["sentiment"]:
            continue
        if f.get("resolution") and c.get("resolution") != f["resolution"]:
            continue
        if f.get("status") and c.get("status") != f["status"]:
            continue
        if f.get("flag") and f["flag"] not in (c.get("flag_list") or []):
            continue
        qa = (c.get("qa") or {}).get("percent")
        if f.get("qa_min") not in (None, "") and (qa is None or qa < float(f["qa_min"])):
            continue
        if f.get("qa_max") not in (None, "") and (qa is None or qa > float(f["qa_max"])):
            continue
        out.append(c)
    return out


def sort_calls(items: list[dict], key: str) -> list[dict]:
    desc = key.startswith("-")
    key = key.lstrip("-")
    getters = {
        "started_at": lambda c: c.get("started_at") or "",
        "duration": lambda c: c.get("duration_sec") or 0,
        "qa": lambda c: (c.get("qa") or {}).get("percent") or -1,
        "sentiment": lambda c: (c.get("sentiment") or {}).get("customer_score") or 0,
        "agent": lambda c: c.get("agent_name") or "",
    }
    return sorted(items, key=getters.get(key, getters["started_at"]), reverse=desc)


def kpis(items: list[dict]) -> dict:
    done = _completed(items)
    qa = [_qa(c) for c in done if _qa(c) is not None]
    durations = [c.get("duration_sec") or 0 for c in done]
    return {
        "total": len(items),
        "completed": len(done),
        "failed": sum(1 for c in items if c.get("status") == "failed"),
        "in_progress": sum(1 for c in items if c.get("status") not in ("completed", "failed")),
        "avg_qa": _r(mean(qa)) if qa else None,
        "qa_pass_rate": _pct(sum(1 for c in done if (c.get("qa") or {}).get("passed")), len(done)),
        "compliance_rate": _pct(sum(1 for c in done if (c.get("compliance") or {}).get("passed")), len(done)),
        "negative_pct": _pct(sum(1 for c in done if (c.get("sentiment") or {}).get("customer") == "negative"), len(done)),
        "unresolved_pct": _pct(sum(1 for c in done if c.get("resolution") in ("unresolved", "escalated")), len(done)),
        "repeat_pct": _pct(sum(1 for c in done if (c.get("flags") or {}).get("repeat_contact")), len(done)),
        "avg_duration": round(mean(durations)) if durations else None,
        "success_rate": _pct(len(done), len(done) + sum(1 for c in items if c.get("status") == "failed")),
    }


def daily_trend(items: list[dict]) -> list[dict]:
    by_day = defaultdict(list)
    for c in _completed(items):
        by_day[c.get("call_date") or (c.get("started_at") or "")[:10]].append(c)
    return [{
        "date": day,
        "calls": len(calls),
        "sentiment": _r(mean((c.get("sentiment") or {}).get("customer_score", 0) for c in calls), 2),
        "qa": _mean_qa(calls),
    } for day, calls in sorted(by_day.items())]


def distribution(items: list[dict], getter) -> dict:
    return dict(Counter(getter(c) for c in _completed(items) if getter(c)))


def reasons(items: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for c in _completed(items):
        groups[(c.get("call_reason"), c.get("call_reason_name") or c.get("call_reason"))].append(c)
    rows = [{
        "id": rid, "name": name, "calls": len(calls),
        "unresolved_pct": _pct(sum(1 for c in calls if c.get("resolution") in ("unresolved", "escalated")), len(calls)),
        "negative_pct": _pct(sum(1 for c in calls if (c.get("sentiment") or {}).get("customer") == "negative"), len(calls)),
        "avg_qa": _mean_qa(calls),
    } for (rid, name), calls in groups.items()]
    return sorted(rows, key=lambda r: r["calls"], reverse=True)


def root_causes(items: list[dict], limit: int = 8) -> list[tuple[str, int]]:
    return Counter(c.get("root_cause") for c in _completed(items)
                   if c.get("root_cause") and c["root_cause"] != "Not determined").most_common(limit)


def criteria_averages(items: list[dict], verdicts: list[dict] | None = None) -> list[dict]:
    """Per QA parameter across calls: Fully met / Partial / Not met / N/A counts, % met (partial = half),
    average score (N/A excluded), verdict, an example quote and the most frequent recommendation."""
    verdicts = verdicts or _verdicts()
    order, stats = [], {}
    for c in _completed(items):
        for q in (c.get("qa") or {}).get("criteria", []):
            if "rating" not in q:  # analysed with an older rubric format
                continue
            st = stats.get(q["id"])
            if st is None:
                st = stats[q["id"]] = {"meta": q, "counts": Counter(), "credits": [], "scores": [], "weights": [],
                                       "quotes": [], "recs": Counter()}
                order.append(q["id"])
            st["counts"][q["rating"]] += 1
            if q.get("credit") is not None:
                st["credits"].append(q["credit"])
                st["scores"].append(q["score"])
            if q.get("evidence"):
                st["quotes"].append((q["rating"] == "met", q["evidence"], c.get("call_id")))
            if q.get("recommendation"):
                st["recs"][q["recommendation"]] += 1
    rows = []
    for cid in order:
        st = stats[cid]
        pct = _r(100 * mean(st["credits"])) if st["credits"] else None
        quote = max(st["quotes"], key=lambda x: x[0], default=None)
        rows.append({
            "id": cid, "name": st["meta"]["name"], "type": st["meta"].get("type"), "category": st["meta"].get("category"),
            "source": st["meta"].get("source", "transcript"),
            "avg": pct,
            "avg_score": _r(mean(st["scores"]), 1) if st["scores"] else None,
            "met": st["counts"]["met"], "partial": st["counts"]["partial"],
            "not_met": st["counts"]["not_met"], "na": st["counts"]["not_applicable"],
            "applicable": len(st["credits"]),
            "verdict": verdict(pct, verdicts),
            "example": quote[1] if quote else "", "example_call": quote[2] if quote else "",
            "recommendation": st["recs"].most_common(1)[0][0] if st["recs"] else "",
        })
    return rows


PRIORITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}


def coaching_summary(items: list[dict], limit: int = 6) -> list[dict]:
    """Coaching recommendations across calls, grouped by title; highest priority and most frequent first."""
    groups: dict[str, dict] = {}
    for c in _completed(items):
        for rec in c.get("coaching") or []:
            key = rec["title"].strip().lower()
            g = groups.setdefault(key, {"title": rec["title"], "priority": rec["priority"], "detail": rec.get("detail", ""),
                                        "calls": 0, "call_ids": []})
            g["calls"] += 1
            g["call_ids"].append(c.get("call_id"))
            if PRIORITY_ORDER.get(rec["priority"], 1) < PRIORITY_ORDER.get(g["priority"], 1):
                g["priority"], g["detail"] = rec["priority"], rec.get("detail", "")
    rows = sorted(groups.values(), key=lambda g: (PRIORITY_ORDER.get(g["priority"], 1), -g["calls"]))
    return rows[:limit]


def compliance_summary(items: list[dict]) -> list[dict]:
    stats = defaultdict(Counter)
    names = {}
    for c in _completed(items):
        for chk in (c.get("compliance") or {}).get("checks", []):
            stats[chk["id"]][chk["status"]] += 1
            names[chk["id"]] = chk["name"]
    rows = []
    for cid, counter in stats.items():
        applicable = counter["pass"] + counter["fail"]
        rows.append({"id": cid, "name": names[cid], "pass": counter["pass"], "fail": counter["fail"],
                     "na": counter["not_applicable"], "pass_rate": _pct(counter["pass"], applicable)})
    return rows


def agents(items: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for c in _completed(items):
        groups[c.get("agent_id") or "unknown"].append(c)
    rows = []
    for agent_id, calls in groups.items():
        crit = [x for x in criteria_averages(calls) if x["avg"] is not None]
        weakest = min(crit, key=lambda x: x["avg"]) if crit else None
        rows.append({
            "agent_id": agent_id,
            "agent_name": calls[0].get("agent_name") or agent_id,
            "team": calls[0].get("team") or "",
            "calls": len(calls),
            "avg_qa": _mean_qa(calls),
            "qa_pass_rate": _pct(sum(1 for c in calls if (c.get("qa") or {}).get("passed")), len(calls)),
            "compliance_rate": _pct(sum(1 for c in calls if (c.get("compliance") or {}).get("passed")), len(calls)),
            "avg_sentiment": _r(mean((c.get("sentiment") or {}).get("customer_score", 0) for c in calls), 2),
            "resolved_pct": _pct(sum(1 for c in calls if c.get("resolution") == "resolved"), len(calls)),
            "avg_duration": round(mean(c.get("duration_sec") or 0 for c in calls)),
            "weakest": weakest,
        })
    return sorted(rows, key=lambda r: r["avg_qa"] or 0, reverse=True)


def flagged(items: list[dict], limit: int = 8) -> list[dict]:
    scored = [(sum(FLAG_WEIGHT.get(f, 1) for f in c.get("flag_list") or []), c) for c in _completed(items)]
    scored = [x for x in scored if x[0] > 0]
    scored.sort(key=lambda x: (x[0], x[1].get("started_at") or ""), reverse=True)
    return [c for _, c in scored[:limit]]


def overview(items: list[dict]) -> dict:
    return {
        "kpis": kpis(items),
        "trend": daily_trend(items),
        "reasons": reasons(items),
        "sentiment": distribution(items, lambda c: (c.get("sentiment") or {}).get("customer")),
        "resolution": distribution(items, lambda c: c.get("resolution")),
        "emotion_end": distribution(items, lambda c: (c.get("emotion") or {}).get("end")),
        "criteria": criteria_averages(items),
        "coaching": coaching_summary(items),
        "compliance": compliance_summary(items),
        "root_causes": root_causes(items),
        "agents": agents(items),
        "flagged": flagged(items),
    }
