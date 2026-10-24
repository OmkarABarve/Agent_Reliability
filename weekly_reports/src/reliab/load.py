"""Read the three inputs the week is joined from: test cases, sessions, send log, spans."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from reliab.models import Engagement, Send, Session, TestCase, parse_time

CASE_COLUMNS = (
    "test_id",
    "category",
    "question",
    "expected_tool",
    "allowed_tools",
    "forbidden_tools",
    "must_include_args",
    "expected_outcome",
    "needs_tool",
)

DEFAULT_TARGETS = {
    "tool": 0.95,
    "args": 0.95,
    "tool_result": 0.95,
    "grounded": 0.98,
    "loop_free": 0.99,
    "p95_ms": 5000,
}

DEFAULT_WEIGHTS = {
    "tool": 0.25,
    "args": 0.15,
    "tool_result": 0.15,
    "grounded": 0.25,
    "loop_free": 0.10,
    "latency": 0.10,
}


def _split_list(value: str | None) -> tuple[str, ...]:
    if not value or not str(value).strip():
        return ()
    return tuple(part.strip() for part in str(value).split(";") if part.strip())


def _split_pairs(value: str | None) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for part in _split_list(value):
        if "=" not in part:
            raise ValueError(f"Expected key=value, got {part!r}")
        key, raw = part.split("=", 1)
        pairs.append((key.strip(), raw.strip()))
    return tuple(pairs)


def _yes(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"yes", "true", "1", "y"}


def load_cases(path: Path) -> list[TestCase]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [name for name in CASE_COLUMNS if name not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path} is missing columns: {', '.join(missing)}")
        cases: list[TestCase] = []
        seen: set[str] = set()
        for line, row in enumerate(reader, start=2):
            test_id = (row.get("test_id") or "").strip()
            if not test_id:
                raise ValueError(f"{path}:{line} has an empty test_id")
            if test_id in seen:
                raise ValueError(f"{path}:{line} repeats test_id {test_id}. Never renumber a case; do not duplicate it.")
            seen.add(test_id)
            cases.append(
                TestCase(
                    test_id=test_id,
                    category=(row.get("category") or "").strip(),
                    question=(row.get("question") or "").strip(),
                    expected_tool=(row.get("expected_tool") or "").strip(),
                    allowed_tools=_split_list(row.get("allowed_tools")),
                    forbidden_tools=_split_list(row.get("forbidden_tools")),
                    must_include_args=_split_pairs(row.get("must_include_args")),
                    expected_outcome=(row.get("expected_outcome") or "").strip(),
                    needs_tool=_yes(row.get("needs_tool")),
                    result_expects=_split_pairs(row.get("result_expects")),
                    score_from_reply=_yes(row.get("score_from_reply")),
                )
            )
    if not cases:
        raise ValueError(f"{path} has no test cases")
    return cases


def load_sessions(path: Path) -> list[Session]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("sessions", [])
    sessions: list[Session] = []
    for row in raw:
        started = parse_time(row["started_at"])
        if started is None:
            raise ValueError(f"Session {row.get('session_id')} has no started_at")
        sessions.append(
            Session(
                session_id=row["session_id"],
                started_at=started,
                reason=row.get("reason") or "",
                model=row["model"],
                prompt_version=str(row["prompt_version"]),
                step_count=int(row["step_count"]),
                agent_version=str(row["agent_version"]),
                ship=bool(row.get("ship", False)),
                project=row.get("project") or "",
            )
        )
    return sessions


def load_sends(path: Path) -> list[Send]:
    sends: list[Send] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            sent_at = parse_time(row["sent_at"])
            if sent_at is None:
                raise ValueError(f"{path}:{line_no} has no sent_at")
            sends.append(
                Send(
                    session_id=row["session_id"],
                    test_id=row["test_id"],
                    run=str(row["run"]),
                    sent_at=sent_at,
                    reply=row.get("reply") or "",
                    trace_id=str(row.get("trace_id") or ""),
                    question=row.get("question") or "",
                )
            )
    return sends


def load_spans(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    payload = json.loads(text)
    if isinstance(payload, dict) and "spans" in payload:
        payload = payload["spans"]
    if not isinstance(payload, list):
        raise ValueError(f"{path} must be a JSON list or JSONL of spans")
    return payload


def load_engagement(path: Path) -> Engagement:
    raw = json.loads(path.read_text(encoding="utf-8"))
    targets = dict(DEFAULT_TARGETS)
    targets.update(raw.get("targets") or {})
    weights = dict(DEFAULT_WEIGHTS)
    weights.update(raw.get("weights") or {})
    return Engagement(
        client=raw["client"],
        agent=raw["agent"],
        environment=raw.get("environment") or "staging",
        project=raw["project"],
        prepared_by=raw.get("prepared_by") or "Agent Reliability",
        week=raw["week"],
        phoenix_base=raw.get("phoenix_base") or "http://127.0.0.1:6006",
        targets=targets,
        weights=weights,
    )
