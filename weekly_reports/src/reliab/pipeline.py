"""Join the send log to Phoenix traces, score every run, write the week.

Stages, in order:
  export or read spans → normalize_spans → join on the send log → score
  → roll up by version → findings → report

Old session rows are never updated. A re-test is a new session id.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from reliab.findings import build_findings, compare_sessions, ship_and_previous
from reliab.models import RULES_VERSION, Engagement, RunRow, Send, Session, TestCase, TraceBundle, week_bounds
from reliab.normalize import normalize_spans
from reliab.report import WeekView, render_html, render_markdown
from reliab.rollup import rate, rollup_versions
from reliab.score import score_run
from reliab.validate import done_checks

UTC = timezone.utc
RESULT_COLUMNS = (
    "session_id",
    "test_id",
    "run",
    "expected_tool",
    "tools_called",
    "tool",
    "args",
    "tool_result",
    "answer",
    "loop",
    "ms",
    "status",
    "trace_id",
    "reasons",
    "category",
    "version_key",
    "prompt_version",
    "model",
    "step_count",
    "ship",
    "human_review",
    "session_reason",
    "reply",
)


def run_week(
    engagement: Engagement,
    cases: list[TestCase],
    sessions: list[Session],
    sends: list[Send],
    spans,
    out_dir: Path,
    judge=None,
) -> WeekView:
    warnings: list[str] = []
    monday, sunday = week_bounds(engagement.week)
    kept: list[Session] = []
    for session in sessions:
        day = session.started_at.astimezone(UTC).date()
        if monday <= day <= sunday:
            kept.append(session)
        else:
            warnings.append(f"{session.session_id} is outside {engagement.week} and was left out.")
    ship_flags = {session.version_key for session in kept if session.ship}
    if len(ship_flags) > 1:
        warnings.append("More than one version is marked ship. The latest marked session decides the week total.")

    traces = normalize_spans(spans)
    joined, join_warnings = _join(cases, kept, sends, traces)
    warnings.extend(join_warnings)
    rows = [score_run(case, session, send, trace, judge=judge) for case, session, send, trace in joined]
    rows.sort(key=lambda row: (row.session_started, row.test_id, row.run))
    untraced = sum(1 for row in rows if row.status == "untraced")
    if untraced:
        warnings.append(
            f"{untraced} sends have no Phoenix trace. They are listed in untraced.csv and left out of the rates."
        )

    rollups = rollup_versions(rows, kept, engagement)
    ship = next(item for item in rollups if item.ship)
    if ship.proposed:
        warnings.append("No session was marked ship. The week total uses the latest version, labelled as proposed.")
    findings, isolated = build_findings(ship.rows)
    previous, current = ship_and_previous(kept, ship.version_key)
    changes = []
    if previous is not None and current is not None and previous.session_id != current.session_id:
        changes = compare_sessions(
            [row for row in rows if row.session_id == previous.session_id],
            [row for row in rows if row.session_id == current.session_id],
        )
    review = _queue_review(ship.rows)
    checks = done_checks(cases, kept, rows, rollups)
    verdict = _verdict(findings, engagement, ship)
    view = WeekView(
        engagement=engagement,
        rows=rows,
        sessions=sorted(kept, key=lambda session: session.started_at),
        rollups=rollups,
        findings=findings,
        isolated=isolated,
        changes=changes,
        previous=previous,
        current=current,
        review=review,
        checks=checks,
        warnings=warnings,
        verdict=verdict,
        generated_at=datetime.now(UTC),
    )
    _write(out_dir, view, spans)
    return view


def _join(cases, sessions, sends, traces: list[TraceBundle]):
    by_case = {case.test_id: case for case in cases}
    by_session = {session.session_id: session for session in sessions}
    by_trace = {trace.trace_id: trace for trace in traces if trace.trace_id}
    by_key = {
        (trace.session_id, trace.test_id, trace.run): trace
        for trace in traces
        if trace.session_id and trace.test_id and trace.run
    }
    joined = []
    warnings: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    metadata_joins = 0
    for send in sends:
        session = by_session.get(send.session_id)
        if session is None:
            continue
        case = by_case.get(send.test_id)
        if case is None:
            warnings.append(f"{send.session_id} {send.test_id} {send.run} is not in the approved test set.")
            continue
        key = (send.session_id, send.test_id, send.run)
        if key in seen:
            warnings.append(f"Duplicate send {send.session_id} {send.test_id} {send.run}. Both rows are kept.")
        seen.add(key)
        trace = None
        if send.trace_id and send.trace_id in by_trace:
            trace = by_trace[send.trace_id]
        elif key in by_key:
            trace = by_key[key]
            metadata_joins += 1
        joined.append((case, session, send, trace))
    if metadata_joins:
        warnings.append(
            f"{metadata_joins} runs joined on session metadata because the send log had no trace id."
        )
    return joined, warnings


def _queue_review(rows: list[RunRow], fraction: float = 0.15) -> list[RunRow]:
    judged = [row for row in rows if row.answer in {"pass", "fail"}]
    if not judged:
        return []
    sample_size = max(1, round(len(judged) * fraction))
    ranked = sorted(
        judged,
        key=lambda row: hashlib.sha256(f"{row.session_id}|{row.test_id}|{row.run}".encode()).hexdigest(),
    )
    chosen = ranked[:sample_size]
    keys = {(row.session_id, row.test_id, row.run) for row in chosen}
    for row in rows:
        row.human_review = (row.session_id, row.test_id, row.run) in keys
    return sorted(chosen, key=lambda row: (row.session_id, row.test_id, row.run))


def _verdict(findings, engagement: Engagement, ship) -> str:
    severities = {finding.severity for finding in findings}
    if "critical" in severities:
        return "Not ready"
    missed = _targets_missed(engagement, ship)
    if "high" in severities or missed or (ship.consistency.fraction is not None and ship.consistency.fraction < 0.90):
        return "Ship with fixes"
    return "Ready"


def _targets_missed(engagement: Engagement, ship) -> bool:
    checks = (
        (rate(ship.rows, "tool").fraction, engagement.target("tool", 0.95), True),
        (rate(ship.rows, "args").fraction, engagement.target("args", 0.95), True),
        (rate(ship.rows, "tool_result").fraction, engagement.target("tool_result", 0.95), True),
        (rate(ship.rows, "answer").fraction, engagement.target("grounded", 0.98), True),
        (rate(ship.rows, "loop").fraction, engagement.target("loop_free", 0.99), True),
    )
    for fraction, target, higher in checks:
        if fraction is None:
            continue
        if higher and fraction < target:
            return True
    if ship.p95_ms is not None and ship.p95_ms > engagement.target("p95_ms", 5000):
        return True
    return False


def _write(out_dir: Path, view: WeekView, spans) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_results(out_dir / "results.csv", view.rows)
    _write_review(out_dir / "review_queue.csv", view.review)
    _write_untraced(out_dir / "untraced.csv", view.rows)
    _write_week_table(out_dir / "week_table.csv", view)
    (out_dir / "findings.json").write_text(
        json.dumps(
            [{**finding.__dict__, "evidence": list(finding.evidence)} for finding in view.findings],
            indent=2,
        ),
        encoding="utf-8",
    )
    ship = next(item for item in view.rollups if item.ship)
    scorecard = {
        "week": view.engagement.week,
        "verdict": view.verdict,
        "score": ship.score,
        "version": ship.version_key,
        "p95_ms": ship.p95_ms,
        "tool": rate(ship.rows, "tool").text(with_percent=True),
        "args": rate(ship.rows, "args").text(with_percent=True),
        "tool_result": rate(ship.rows, "tool_result").text(with_percent=True),
        "grounded": rate(ship.rows, "answer").text(with_percent=True),
        "loop": rate(ship.rows, "loop").text(with_percent=True),
        "consistency": ship.consistency.text(with_percent=True),
        "rows": len(view.rows),
    }
    (out_dir / "scorecard.json").write_text(json.dumps(scorecard, indent=2), encoding="utf-8")
    (out_dir / "report.html").write_text(render_html(view), encoding="utf-8")
    (out_dir / "report.md").write_text(render_markdown(view), encoding="utf-8")
    manifest = {
        "rules_version": RULES_VERSION,
        "week": view.engagement.week,
        "generated_at": view.generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "client": view.engagement.client,
        "project": view.engagement.project,
        "ship_version": ship.version_key,
        "verdict": view.verdict,
        "row_count": len(view.rows),
        "span_count": len(list(spans)) if not hasattr(spans, "to_dict") else None,
        "spans_sha256": _hash_spans(spans),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _write_results(path: Path, rows: list[RunRow]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "session_id": row.session_id,
                    "test_id": row.test_id,
                    "run": row.run,
                    "expected_tool": row.expected_tool,
                    "tools_called": row.tools_called,
                    "tool": row.tool,
                    "args": row.args,
                    "tool_result": row.tool_result,
                    "answer": row.answer,
                    "loop": row.loop,
                    "ms": "" if row.ms is None else row.ms,
                    "status": row.status,
                    "trace_id": row.trace_id,
                    "reasons": ";".join(row.reasons),
                    "category": row.category,
                    "version_key": row.version_key,
                    "prompt_version": row.prompt_version,
                    "model": row.model,
                    "step_count": row.step_count,
                    "ship": "yes" if row.ship else "no",
                    "human_review": "yes" if row.human_review else "no",
                    "session_reason": row.session_reason,
                    "reply": row.reply,
                }
            )


def _write_review(path: Path, rows: list[RunRow]) -> None:
    fields = ("session_id", "test_id", "run", "trace_id", "question", "reply", "tool_result_text", "answer")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: getattr(row, name) for name in fields})


def _write_untraced(path: Path, rows: list[RunRow]) -> None:
    fields = ("session_id", "test_id", "run", "sent_reply", "status")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            if row.status != "untraced":
                continue
            writer.writerow(
                {
                    "session_id": row.session_id,
                    "test_id": row.test_id,
                    "run": row.run,
                    "sent_reply": row.reply,
                    "status": row.status,
                }
            )


def _write_week_table(path: Path, view: WeekView) -> None:
    fields = ("version_key", "ship", "scope", "session_id", "test_id", "dimension", "passed", "total")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for item in view.rollups:
            groups = [("week", "week", item.rows)]
            for session in item.sessions:
                groups.append(("session", session.session_id, [row for row in item.rows if row.session_id == session.session_id]))
            test_ids = sorted({row.test_id for row in item.rows})
            for scope, session_id, subset in groups:
                if scope == "week" and not item.ship:
                    continue
                for test_id in ["ALL", *test_ids]:
                    rows = subset if test_id == "ALL" else [row for row in subset if row.test_id == test_id]
                    for dimension in ("tool", "args", "tool_result", "answer", "loop"):
                        measured = rate(rows, dimension)
                        writer.writerow(
                            {
                                "version_key": item.version_key,
                                "ship": "yes" if item.ship else "no",
                                "scope": scope,
                                "session_id": session_id,
                                "test_id": test_id,
                                "dimension": dimension,
                                "passed": measured.passed,
                                "total": measured.total,
                            }
                        )


def _hash_spans(spans) -> str:
    try:
        payload = json.dumps(spans, default=str, sort_keys=True).encode("utf-8")
    except TypeError:
        payload = repr(spans).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
