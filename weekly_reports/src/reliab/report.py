"""Client report. Phoenix stays the evidence locker. This file is what gets read."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime

from reliab.findings import PLAIN, Change, Finding
from reliab.models import Engagement, RunRow, Session
from reliab.rollup import (
    VersionRollup,
    direction,
    latency_direction,
    percentile,
    rate,
    target_status,
)
from reliab.validate import Check

_CSS = """
:root {
  --bg: #f3efe6;
  --paper: #fffdf8;
  --ink: #1e1a16;
  --muted: #5e584f;
  --line: #e4dccf;
  --accent: #0e6b67;
  --critical: #8d2433;
  --high: #8a5a12;
  --met: #1d6b3c;
  --below: #8d2433;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 16px/1.5 "Segoe UI", "Helvetica Neue", sans-serif;
}
main { max-width: 880px; margin: 0 auto; padding: 48px 24px 80px; }
h1, h2, h3 { font-family: Palatino, "Palatino Linotype", Georgia, serif; font-weight: 600; letter-spacing: -0.02em; }
h1 { font-size: 40px; line-height: 1.1; margin: 0 0 8px; }
h2 { font-size: 26px; margin: 40px 0 12px; }
h3 { font-size: 18px; margin: 0 0 8px; }
p { margin: 0 0 12px; }
.kicker { color: var(--accent); font-size: 13px; letter-spacing: 0.14em; text-transform: uppercase; margin-bottom: 12px; }
.meta { color: var(--muted); margin-bottom: 28px; }
.grid { display: grid; grid-template-columns: 180px 1fr; gap: 28px; align-items: start; }
.score { font-family: Palatino, Georgia, serif; font-size: 72px; line-height: 0.9; color: var(--accent); }
.score span { display: block; font-family: "Segoe UI", sans-serif; font-size: 14px; color: var(--muted); margin-top: 8px; }
.verdict { display: inline-block; border: 1px solid var(--ink); padding: 2px 10px; border-radius: 999px; font-size: 14px; }
.verdict.not-ready { border-color: var(--critical); color: var(--critical); }
.verdict.ship-with-fixes { border-color: var(--high); color: var(--high); }
.verdict.ready { border-color: var(--met); color: var(--met); }
table { width: 100%; border-collapse: collapse; background: var(--paper); margin: 8px 0 18px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; font-size: 14px; }
th { color: var(--muted); font-weight: 600; font-size: 12px; letter-spacing: 0.04em; text-transform: uppercase; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.card { background: var(--paper); border: 1px solid var(--line); border-left: 4px solid var(--accent); padding: 16px 18px; margin: 0 0 14px; }
.card.critical { border-left-color: var(--critical); }
.card.high { border-left-color: var(--high); }
.card.medium { border-left-color: #b0893f; }
.card.low { border-left-color: var(--muted); }
.tag { font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }
.met { color: var(--met); }
.below { color: var(--below); }
.note { color: var(--muted); font-size: 14px; }
code, .mono { font-family: Consolas, "Courier New", monospace; font-size: 13px; }
ul { margin: 0 0 12px; padding-left: 18px; }
a { color: var(--accent); }
.check { list-style: none; padding: 0; }
.check li { padding: 6px 0; border-bottom: 1px solid var(--line); }
@media print {
  body { background: white; }
  main { max-width: none; padding: 0; }
  .card, table { break-inside: avoid; }
}
"""


@dataclass
class WeekView:
    engagement: Engagement
    rows: list[RunRow]
    sessions: list[Session]
    rollups: list[VersionRollup]
    findings: list[Finding]
    isolated: list[RunRow]
    changes: list[Change]
    previous: Session | None
    current: Session | None
    review: list[RunRow]
    checks: list[Check]
    warnings: list[str]
    verdict: str
    generated_at: datetime


def render_html(view: WeekView) -> str:
    title = f"{view.engagement.client} · {view.engagement.week}"
    body = "\n".join(
        [
            _cover(view),
            _summary(view),
            _method(view),
            _scorecard(view, html_mode=True),
            _categories(view),
            _week_tables(view, html_mode=True),
            _changes(view, html_mode=True),
            _findings(view, html_mode=True),
            _roadmap(view, html_mode=True),
            _review(view, html_mode=True),
            _checks(view, html_mode=True),
            _appendix(view),
        ]
    )
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )


def render_markdown(view: WeekView) -> str:
    parts = [
        _markdown_cover(view),
        _markdown_summary(view),
        _method(view, html_mode=False),
        _scorecard(view, html_mode=False),
        _categories(view, html_mode=False),
        _week_tables(view, html_mode=False),
        _changes(view, html_mode=False),
        _findings(view, html_mode=False),
        _roadmap(view, html_mode=False),
        _review(view, html_mode=False),
        _checks(view, html_mode=False),
        _appendix(view, html_mode=False),
    ]
    return "\n\n".join(part.strip() for part in parts if part.strip()) + "\n"


def _ship(view: WeekView) -> VersionRollup:
    return next(item for item in view.rollups if item.ship)


def _rows(view: WeekView, session: Session | None) -> list[RunRow]:
    if session is None:
        return []
    return [row for row in view.rows if row.session_id == session.session_id]


def _cover(view: WeekView) -> str:
    engagement = view.engagement
    ship = _ship(view)
    monday, sunday = _bounds(view)
    proposed = " Proposed, because no session was marked ship." if ship.proposed else ""
    return f"""
<p class="kicker">Agent Reliability · Weekly evaluation</p>
<h1>{html.escape(engagement.client)}</h1>
<p class="meta">{html.escape(engagement.agent)} · {html.escape(engagement.environment)} · project <span class="mono">{html.escape(engagement.project)}</span><br>
Week {html.escape(engagement.week)} · {monday} to {sunday}<br>
Ship version <span class="mono">{html.escape(ship.version_key)}</span>.{html.escape(proposed)}<br>
Prepared by {html.escape(engagement.prepared_by)} · {view.generated_at.strftime("%d %b %Y")}</p>
"""


def _summary(view: WeekView) -> str:
    ship = _ship(view)
    risks = [html.escape(item.title) for item in view.findings[:3]]
    if _latency_over(view) and len(risks) < 3:
        risks.append(f"p95 reply time is {_seconds(ship.p95_ms)}, above the target.")
    if not risks:
        risks.append("No repeating failure pattern on the ship version.")
    fixes = [html.escape(item.fix) for item in view.findings[:3]]
    if not fixes:
        fixes.append("Keep this test set on the next release.")
    risk_items = "".join(f"<li>{item}</li>" for item in risks)
    fix_items = "".join(f"<li>{item}</li>" for item in fixes)
    trend = html.escape(_trend_sentence(view))
    because = html.escape(_verdict_because(view))
    provisional = ""
    if view.review:
        provisional = (
            f"<p class=\"note\">The grounded number is provisional until "
            f"{len(view.review)} queued runs are signed.</p>"
        )
    return f"""
<h2>Executive summary</h2>
<div class="grid">
  <div class="score">{ship.score if ship.score is not None else "—"}<span>out of 100</span></div>
  <div>
    <p><span class="verdict {html.escape(view.verdict.lower().replace(" ", "-"))}">{html.escape(view.verdict)}</span></p>
    <p>{because} {trend}</p>
    {provisional}
  </div>
</div>
<p><strong>Top risks</strong></p>
<ul>{risk_items}</ul>
<p><strong>Top fixes</strong></p>
<ul>{fix_items}</ul>
"""


def _markdown_cover(view: WeekView) -> str:
    engagement = view.engagement
    ship = _ship(view)
    monday, sunday = _bounds(view)
    proposed = " Proposed, because no session was marked ship." if ship.proposed else ""
    return (
        f"# {engagement.client}\n\n"
        f"Agent Reliability weekly evaluation. {engagement.agent} · {engagement.environment} · "
        f"project `{engagement.project}`.\n\n"
        f"Week {engagement.week} · {monday} to {sunday}.\n\n"
        f"Ship version `{ship.version_key}`.{proposed}\n\n"
        f"Prepared by {engagement.prepared_by} · {view.generated_at.strftime('%d %b %Y')}."
    )


def _markdown_summary(view: WeekView) -> str:
    ship = _ship(view)
    risks = [item.title for item in view.findings[:3]]
    if _latency_over(view) and len(risks) < 3:
        risks.append(f"p95 reply time is {_seconds(ship.p95_ms)}, above the target.")
    if not risks:
        risks.append("No repeating failure pattern on the ship version.")
    fixes = [item.fix for item in view.findings[:3]] or ["Keep this test set on the next release."]
    lines = [
        "## Executive summary",
        "",
        f"**{ship.score}/100 · {view.verdict}**",
        "",
        _verdict_because(view),
        "",
        _trend_sentence(view),
        "",
    ]
    if view.review:
        lines.append(f"The grounded number is provisional until {len(view.review)} queued runs are signed.")
        lines.append("")
    lines.append("Top risks:")
    lines.append("")
    lines.extend(f"- {item}" for item in risks)
    lines.append("")
    lines.append("Top fixes:")
    lines.append("")
    lines.extend(f"- {item}" for item in fixes)
    return "\n".join(lines)


def _method(view: WeekView, html_mode: bool = True) -> str:
    ship = _ship(view)
    text = (
        f"Each approved case was sent three times inside a named session. "
        f"Traces come from one Phoenix project, {view.engagement.project}. "
        f"The send log joins to those traces on trace id, or on session id + test id + run. "
        f"Tool, arguments, tool result, answer, and loops are scored as separate columns. "
        f"The answer column is a rule check that the reply only names facts in the tool result. "
        f"A person still reviews the queued sample before that number is final. "
        f"The week total is only {ship.version_key}. Other sittings stay in their own columns."
    )
    if not html_mode:
        return "## Scope and method\n\n" + text
    return f"<h2>Scope and method</h2><p>{html.escape(text)}</p>"


def _scorecard(view: WeekView, html_mode: bool) -> str:
    ship = _ship(view)
    previous = _rows(view, view.previous)
    current = _rows(view, view.current)
    rows = [
        _metric("Right tool", rate(ship.rows, "tool"), view.engagement.target("tool", 0.95), rate(previous, "tool"), rate(current, "tool")),
        _metric("Right arguments", rate(ship.rows, "args"), view.engagement.target("args", 0.95), rate(previous, "args"), rate(current, "args")),
        _metric("Tool result", rate(ship.rows, "tool_result"), view.engagement.target("tool_result", 0.95), rate(previous, "tool_result"), rate(current, "tool_result")),
        _metric("Grounded", rate(ship.rows, "answer"), view.engagement.target("grounded", 0.98), rate(previous, "answer"), rate(current, "answer")),
        _metric("No loop", rate(ship.rows, "loop"), view.engagement.target("loop_free", 0.99), rate(previous, "loop"), rate(current, "loop")),
        _latency_metric(view, previous, current),
        _consistency_metric(ship, previous, current),
    ]
    if html_mode:
        body = "".join(
            "<tr>"
            f"<td>{html.escape(item['name'])}</td>"
            f"<td class=\"num\">{html.escape(item['result'])}</td>"
            f"<td class=\"num\">{html.escape(item['target'])}</td>"
            f"<td class=\"{html.escape(item['status'].lower())}\">{html.escape(item['status'])}</td>"
            f"<td>{html.escape(item['trend'])}</td>"
            "</tr>"
            for item in rows
        )
        return (
            "<h2>Scorecard</h2>"
            "<p class=\"note\">Result is the ship version. Trend compares the previous sitting with the latest ship sitting. Up means it got better.</p>"
            "<table><thead><tr><th>Dimension</th><th class=\"num\">Result</th><th class=\"num\">Target</th><th>Status</th><th>Trend</th></tr></thead>"
            f"<tbody>{body}</tbody></table>"
        )
    lines = ["## Scorecard", "", "| Dimension | Result | Target | Status | Trend |", "| --- | --- | --- | --- | --- |"]
    for item in rows:
        lines.append(f"| {item['name']} | {item['result']} | {item['target']} | {item['status']} | {item['trend']} |")
    return "\n".join(lines)


def _categories(view: WeekView, html_mode: bool = True) -> str:
    ship = _ship(view)
    if not ship.category_tool:
        return ""
    if html_mode:
        body = "".join(
            f"<tr><td>{html.escape(name)}</td><td class=\"num\">{html.escape(measured.text(with_percent=True))}</td></tr>"
            for name, measured in ship.category_tool.items()
        )
        return (
            "<h2>Right tool by category</h2>"
            "<table><thead><tr><th>Category</th><th class=\"num\">Ship version</th></tr></thead>"
            f"<tbody>{body}</tbody></table>"
        )
    lines = ["## Right tool by category", "", "| Category | Ship version |", "| --- | --- |"]
    for name, measured in ship.category_tool.items():
        lines.append(f"| {name} | {measured.text(with_percent=True)} |")
    return "\n".join(lines)


def _week_tables(view: WeekView, html_mode: bool) -> str:
    blocks = ["<h2>Week table</h2>" if html_mode else "## Week table"]
    for item in view.rollups:
        label = "Week total, ship version" if item.ship else "Earlier version, not in the week total"
        if item.proposed and item.ship:
            label = "Proposed week total (latest version)"
        blocks.append(_one_version_table(item, label, html_mode))
    separator = "\n" if html_mode else "\n\n"
    return separator.join(blocks)


def _one_version_table(item: VersionRollup, label: str, html_mode: bool) -> str:
    sessions = item.sessions
    test_ids = sorted({row.test_id for row in item.rows})
    dimensions = (("tool", "tool"), ("tool_result", "result"), ("answer", "answer"))
    header = [label] + [session.label for session in sessions]
    if item.ship:
        header.append("Week")
    data_rows: list[list[str]] = []
    for test_id in test_ids:
        for dimension, name in dimensions:
            cells = [f"{test_id} {name}"]
            for session in sessions:
                subset = [row for row in item.rows if row.session_id == session.session_id and row.test_id == test_id]
                cells.append(rate(subset, dimension).text(with_percent=False))
            if item.ship:
                subset = [row for row in item.rows if row.test_id == test_id]
                cells.append(rate(subset, dimension).text(with_percent=True))
            data_rows.append(cells)
    for dimension, name in (
        ("tool", "All cases, right tool"),
        ("args", "All cases, right args"),
        ("tool_result", "All cases, tool result"),
        ("answer", "All cases, grounded"),
        ("loop", "All cases, no loop"),
    ):
        cells = [name]
        for session in sessions:
            subset = [row for row in item.rows if row.session_id == session.session_id]
            cells.append(rate(subset, dimension).text(with_percent=True))
        if item.ship:
            cells.append(rate(item.rows, dimension).text(with_percent=True))
        data_rows.append(cells)
    caption = f"{item.version_key}"
    if html_mode:
        head = "".join(f"<th>{html.escape(cell)}</th>" for cell in header)
        body = "".join(
            "<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>"
            for row in data_rows
        )
        return f"<p class=\"note\">{html.escape(caption)}</p><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    lines = [f"**{caption}**", "", "| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    for row in data_rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _changes(view: WeekView, html_mode: bool) -> str:
    if view.previous is None or view.current is None:
        return ""
    intro = (
        f"Re-test sitting {view.current.session_id} against {view.previous.session_id}. "
        "Old rows were not edited. A pattern is two or more fails of the same case and reason."
    )
    if not view.changes:
        body = "No pattern moved between these sittings."
        if html_mode:
            return f"<h2>Before and after</h2><p>{html.escape(intro)}</p><p>{html.escape(body)}</p>"
        return f"## Before and after\n\n{intro}\n\n{body}"
    if html_mode:
        rows = "".join(
            "<tr>"
            f"<td>{html.escape(item.kind)}</td>"
            f"<td>{html.escape(item.test_id)}</td>"
            f"<td>{html.escape(PLAIN.get(item.reason, item.reason))}</td>"
            f"<td class=\"num\">{item.before}/{item.before_runs}</td>"
            f"<td class=\"num\">{item.after}/{item.after_runs}</td>"
            "</tr>"
            for item in view.changes
        )
        return (
            f"<h2>Before and after</h2><p>{html.escape(intro)}</p>"
            "<table><thead><tr><th>Change</th><th>Case</th><th>Pattern</th>"
            f"<th class=\"num\">{html.escape(view.previous.label)}</th>"
            f"<th class=\"num\">{html.escape(view.current.label)}</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    lines = [
        "## Before and after",
        "",
        intro,
        "",
        f"| Change | Case | Pattern | {view.previous.label} | {view.current.label} |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in view.changes:
        lines.append(
            f"| {item.kind} | {item.test_id} | {PLAIN.get(item.reason, item.reason)} | {item.before}/{item.before_runs} | {item.after}/{item.after_runs} |"
        )
    return "\n".join(lines)


def _findings(view: WeekView, html_mode: bool) -> str:
    if html_mode:
        cards = []
        for finding in view.findings:
            evidence = ", ".join(finding.evidence) or "no trace id"
            cards.append(
                f"<article class=\"card {html.escape(finding.severity)}\">"
                f"<p class=\"tag\">{html.escape(finding.finding_id)} · {html.escape(finding.severity)}</p>"
                f"<h3>{html.escape(finding.title)}</h3>"
                f"<p>{html.escape(finding.what)}</p>"
                f"<p><strong>Frequency.</strong> {html.escape(finding.frequency)}</p>"
                f"<p><strong>Impact.</strong> {html.escape(finding.impact)}</p>"
                f"<p><strong>Evidence.</strong> <span class=\"mono\">{html.escape(evidence)}</span></p>"
                f"<p><strong>Root cause.</strong> {html.escape(finding.cause)}</p>"
                f"<p><strong>Fix.</strong> {html.escape(finding.fix)} · {html.escape(finding.effort)} · {html.escape(finding.owner)}</p>"
                "</article>"
            )
        isolated = _isolated(view, html_mode=True)
        body = "".join(cards) if cards else "<p>No repeating pattern on the ship version.</p>"
        return f"<h2>Findings</h2><p class=\"note\">Patterns on the ship version only. A single miss is listed under isolated misses, not as a finding.</p>{body}{isolated}"
    lines = ["## Findings", ""]
    if not view.findings:
        lines.append("No repeating pattern on the ship version.")
    for finding in view.findings:
        evidence = ", ".join(finding.evidence) or "no trace id"
        lines.extend(
            [
                f"### {finding.finding_id} {finding.title} ({finding.severity})",
                "",
                finding.what,
                "",
                f"Frequency: {finding.frequency}",
                "",
                f"Impact: {finding.impact}",
                "",
                f"Evidence: {evidence}",
                "",
                f"Root cause: {finding.cause}",
                "",
                f"Fix: {finding.fix} · {finding.effort} · {finding.owner}",
                "",
            ]
        )
    lines.append(_isolated(view, html_mode=False))
    return "\n".join(lines)


def _isolated(view: WeekView, html_mode: bool) -> str:
    if not view.isolated:
        text = "No isolated misses on the ship version."
        return f"<p class=\"note\">{text}</p>" if html_mode else text
    if html_mode:
        items = "".join(
            f"<li><span class=\"mono\">{html.escape(row.trace_id or row.run)}</span> "
            f"{html.escape(row.test_id)} {html.escape(row.run)} · {html.escape(PLAIN.get(row.primary_reason, row.primary_reason))}</li>"
            for row in view.isolated
        )
        return f"<h3>Isolated misses</h3><ul>{items}</ul>"
    lines = ["### Isolated misses", ""]
    for row in view.isolated:
        lines.append(f"- {row.trace_id or row.run}: {row.test_id} {row.run} · {PLAIN.get(row.primary_reason, row.primary_reason)}")
    return "\n".join(lines)


def _roadmap(view: WeekView, html_mode: bool) -> str:
    if not view.findings:
        return ""
    if html_mode:
        body = "".join(
            "<tr>"
            f"<td>{html.escape(item.when)}</td>"
            f"<td>{html.escape(item.fix)}</td>"
            f"<td>{html.escape(item.finding_id)}</td>"
            f"<td>{html.escape(item.effort)}</td>"
            f"<td>{html.escape(item.owner)}</td>"
            "</tr>"
            for item in view.findings
        )
        return (
            "<h2>Recommendations</h2>"
            "<table><thead><tr><th>When</th><th>Action</th><th>Fixes</th><th>Effort</th><th>Owner</th></tr></thead>"
            f"<tbody>{body}</tbody></table>"
        )
    lines = ["## Recommendations", "", "| When | Action | Fixes | Effort | Owner |", "| --- | --- | --- | --- | --- |"]
    for item in view.findings:
        lines.append(f"| {item.when} | {item.fix} | {item.finding_id} | {item.effort} | {item.owner} |")
    return "\n".join(lines)


def _review(view: WeekView, html_mode: bool) -> str:
    ship_rows = [row for row in _ship(view).rows if row.answer in {"pass", "fail"}]
    if html_mode:
        body = "".join(
            "<tr>"
            f"<td class=\"mono\">{html.escape(row.session_id)}</td>"
            f"<td>{html.escape(row.test_id)} {html.escape(row.run)}</td>"
            f"<td class=\"mono\">{html.escape(row.trace_id)}</td>"
            f"<td>{html.escape(_clip(row.reply, 90))}</td>"
            "</tr>"
            for row in view.review
        )
        return (
            "<h2>Review queue</h2>"
            f"<p>About 15% of the ship-version answers are queued ({len(view.review)} of {len(ship_rows)}). "
            "Sign these before the grounded number is treated as final. The full queue is review_queue.csv.</p>"
            "<table><thead><tr><th>Session</th><th>Run</th><th>Trace</th><th>Reply</th></tr></thead>"
            f"<tbody>{body}</tbody></table>"
        )
    lines = [
        "## Review queue",
        "",
        f"{len(view.review)} of {len(ship_rows)} ship-version answers are queued. See review_queue.csv.",
        "",
    ]
    for row in view.review:
        lines.append(f"- {row.session_id} {row.test_id} {row.run} · {row.trace_id}")
    return "\n".join(lines)


def _checks(view: WeekView, html_mode: bool) -> str:
    if html_mode:
        items = "".join(
            f"<li>{'Pass' if check.passed else 'Fail'} · {html.escape(check.name)}. {html.escape(check.detail)}</li>"
            for check in view.checks
        )
        extra = ""
        if view.warnings:
            extra = "<ul>" + "".join(f"<li>{html.escape(warning)}</li>" for warning in view.warnings) + "</ul>"
        return f"<h2>Done when</h2><ul class=\"check\">{items}</ul>{extra}"
    lines = ["## Done when", ""]
    for check in view.checks:
        mark = "Pass" if check.passed else "Fail"
        lines.append(f"- {mark} · {check.name}. {check.detail}")
    for warning in view.warnings:
        lines.append(f"- Note · {warning}")
    return "\n".join(lines)


def _appendix(view: WeekView, html_mode: bool = True) -> str:
    base = view.engagement.phoenix_base.rstrip("/")
    project = view.engagement.project
    sample = next((row.trace_id for row in view.rows if row.trace_id), "")
    link = f"{base}/projects/{project}/traces/{sample}" if sample else base
    text = (
        "results.csv is one row per run and is the source of the totals above. "
        "week_table.csv is the same grid in long form. "
        "findings.json and scorecard.json are the structured copies. "
        f"A trace opens at {link} when this Phoenix base can see the project. "
        "The report quotes session ids and trace ids."
    )
    if html_mode:
        return f"<h2>Appendix</h2><p>{html.escape(text)}</p>"
    return "## Appendix\n\n" + text


def _metric(name, measured, target, before, after) -> dict:
    trend = direction(before.fraction if before else None, after.fraction if after else None)
    return {
        "name": name,
        "result": measured.text(with_percent=True),
        "target": f"≥ {round(target * 100)}%",
        "status": target_status(measured.fraction, target),
        "trend": _trend_word(trend),
    }


def _latency_metric(view: WeekView, previous: list[RunRow], current: list[RunRow]) -> dict:
    ship = _ship(view)
    target = view.engagement.target("p95_ms", 5000)
    before = percentile([row.ms for row in previous if row.ms is not None], 0.95)
    after = percentile([row.ms for row in current if row.ms is not None], 0.95)
    status = "n/a" if ship.p95_ms is None else ("Met" if ship.p95_ms <= target else "Below")
    return {
        "name": "p95 latency",
        "result": _seconds(ship.p95_ms),
        "target": f"≤ {_seconds(int(target))}",
        "status": status,
        "trend": _trend_word(latency_direction(before, after)),
    }


def _consistency_metric(ship: VersionRollup, previous: list[RunRow], current: list[RunRow]) -> dict:
    from reliab.rollup import _consistency

    before = _consistency(previous) if previous else None
    after = _consistency(current) if current else None
    trend = direction(
        before.fraction if before else None,
        after.fraction if after else None,
    )
    return {
        "name": "Consistency",
        "result": ship.consistency.text(with_percent=True),
        "target": "≥ 90%",
        "status": target_status(ship.consistency.fraction, 0.90),
        "trend": _trend_word(trend),
    }


def _verdict_because(view: WeekView) -> str:
    critical = [finding for finding in view.findings if finding.severity == "critical"]
    if view.verdict == "Not ready" and critical:
        return f"Not ready, because {critical[0].finding_id} is a critical pattern on the version you would ship."
    if view.verdict == "Ship with fixes":
        return "Ship with fixes: nothing critical is open, and the highs have a place on this week's list."
    return "Ready: no critical or high pattern, and the key targets are met."


def _trend_sentence(view: WeekView) -> str:
    ship = _ship(view)
    if view.previous is None or view.current is None:
        return f"Ship version score is {ship.score}/100. This week has a single sitting."
    previous_rows = _rows(view, view.previous)
    current_rows = _rows(view, view.current)
    before_tool = rate(previous_rows, "tool")
    after_tool = rate(current_rows, "tool")
    before_answer = rate(previous_rows, "answer")
    after_answer = rate(current_rows, "answer")
    previous_score = _version_score(view, view.previous)
    return (
        f"Right tool moved from {before_tool.text(with_percent=True)} in {view.previous.session_id} "
        f"to {after_tool.text(with_percent=True)} in {view.current.session_id}. "
        f"Grounded moved from {before_answer.text(with_percent=True)} to {after_answer.text(with_percent=True)}. "
        f"The ship version scores {ship.score}/100"
        + (f"; the previous sitting scored {previous_score}/100." if previous_score is not None else ".")
    )


def _version_score(view: WeekView, session: Session) -> int | None:
    for item in view.rollups:
        if any(candidate.session_id == session.session_id for candidate in item.sessions):
            if len(item.sessions) == 1:
                return item.score
    return None


def _latency_over(view: WeekView) -> bool:
    ship = _ship(view)
    if ship.p95_ms is None:
        return False
    return ship.p95_ms > view.engagement.target("p95_ms", 5000)


def _seconds(value: int | None) -> str:
    if value is None:
        return "n/a"
    return f"{value / 1000:.1f} s"


def _trend_word(name: str) -> str:
    return {"up": "↑", "down": "↓", "flat": "→", "new": "new"}.get(name, "new")


def _bounds(view: WeekView) -> tuple[str, str]:
    from reliab.models import week_bounds

    monday, sunday = week_bounds(view.engagement.week)
    return monday.strftime("%d %b %Y"), sunday.strftime("%d %b %Y")


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
