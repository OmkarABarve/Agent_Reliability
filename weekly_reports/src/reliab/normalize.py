"""Turn a Phoenix span export into one bundle per trace.

`get_spans_dataframe` rows are flat, with dotted columns such as
`attributes.tool.name`. A hand-built export may nest `attributes` instead.
Both shapes are accepted. Tool rows are those with span kind TOOL, or any
row that carries a tool name.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone

from reliab.models import ToolCall, TraceBundle, parse_time

META_KEYS = (
    "session_id",
    "test_id",
    "run",
    "agent_version",
    "prompt_version",
    "step_count",
    "model",
)


def is_tool(span: dict) -> bool:
    kind = str(
        _get(span, "span_kind")
        or _get(span, "attributes.openinference.span.kind")
        or ""
    ).upper()
    if kind == "TOOL":
        return True
    return bool(_get(span, "attributes.tool.name") or _get(span, "tool.name"))


def normalize_spans(spans) -> list[TraceBundle]:
    rows = _coerce(spans)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        trace_id = str(
            _get(row, "context.trace_id")
            or _get(row, "trace_id")
            or ""
        )
        if not trace_id:
            continue
        grouped[trace_id].append(row)

    bundles: list[TraceBundle] = []
    for trace_id, members in grouped.items():
        meta: dict = {}
        for member in members:
            for key, value in _metadata(member).items():
                if value not in (None, "") and key not in meta:
                    meta[key] = value
        tools = [_tool_call(member) for member in members if is_tool(member)]
        tools.sort(
            key=lambda call: (
                call.started_at is None,
                call.started_at or datetime.min.replace(tzinfo=timezone.utc),
                call.span_id,
            )
        )
        bundles.append(
            TraceBundle(
                trace_id=trace_id,
                session_id=str(meta.get("session_id") or ""),
                test_id=str(meta.get("test_id") or ""),
                run=str(meta.get("run") or ""),
                tools=tools,
                answer=_answer(members),
                latency_ms=_latency(members),
                metadata=meta,
            )
        )
    return bundles


def _coerce(spans) -> list[dict]:
    if hasattr(spans, "to_dict") and hasattr(spans, "columns"):
        frame = spans
        columns = frame.columns
        if getattr(columns, "nlevels", 1) > 1:
            frame = frame.copy()
            frame.columns = [
                ".".join(str(part) for part in column if str(part) not in {"", "None"})
                for column in frame.columns
            ]
        records = frame.to_dict(orient="records")
        return [{key: _clean(value) for key, value in record.items()} for record in records]
    return list(spans)


def _clean(value):
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _get(span: dict, path: str):
    if path in span and span[path] is not None:
        return _clean(span[path])
    current = span
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return _clean(current)


def _metadata(span: dict) -> dict:
    raw = _get(span, "attributes.metadata")
    if raw is None:
        raw = _get(span, "metadata")
    meta = _as_dict(raw)
    for key in META_KEYS:
        flat = _get(span, f"attributes.metadata.{key}")
        if flat is not None and key not in meta:
            meta[key] = flat
    if not meta.get("session_id"):
        session_id = _get(span, "attributes.session.id") or _get(span, "session.id")
        if session_id:
            meta["session_id"] = session_id
    return meta


def _as_dict(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _jsonish(value):
    if isinstance(value, (dict, list)) or value is None:
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return value
    return value


def _tool_call(span: dict) -> ToolCall:
    name = _get(span, "attributes.tool.name") or _get(span, "tool.name") or span.get("name") or ""
    args = _jsonish(_get(span, "attributes.input.value") or _get(span, "attributes.tool.parameters"))
    result = _jsonish(_get(span, "attributes.output.value") or _get(span, "attributes.output"))
    span_id = str(_get(span, "context.span_id") or _get(span, "span_id") or "")
    return ToolCall(
        name=str(name),
        args=args if isinstance(args, dict) else {"input": args} if args not in (None, "") else {},
        result=result,
        span_id=span_id,
        started_at=parse_time(_as_time(_get(span, "start_time"))),
        latency_ms=_duration_ms(span),
    )


def _answer(spans: list[dict]) -> str:
    ranked: list[tuple[int, str]] = []
    for span in spans:
        if is_tool(span):
            continue
        text = _output_text(span)
        if not text:
            continue
        kind = str(_get(span, "span_kind") or "").upper()
        parent = _get(span, "parent_id")
        root = parent in (None, "", "None")
        rank = 0
        if kind == "AGENT":
            rank = 3
        elif kind == "CHAIN" and root:
            rank = 2
        elif root:
            rank = 1
        ranked.append((rank, text))
    if not ranked:
        return ""
    ranked.sort(key=lambda item: item[0])
    return ranked[-1][1]


def _output_text(span: dict) -> str:
    value = _jsonish(
        _get(span, "attributes.output.value")
        or _get(span, "attributes.output")
        or _get(span, "output.value")
    )
    if isinstance(value, dict):
        for key in ("answer", "content", "text"):
            if key in value and isinstance(value[key], str):
                return value[key]
        return ""
    if isinstance(value, str):
        return value
    return ""


def _latency(spans: list[dict]) -> int | None:
    best: tuple[int, int] | None = None
    for span in spans:
        if is_tool(span):
            continue
        duration = _duration_ms(span)
        if duration is None:
            continue
        kind = str(_get(span, "span_kind") or "").upper()
        rank = 3 if kind == "AGENT" else 2 if kind == "CHAIN" else 1
        if best is None or rank > best[0]:
            best = (rank, duration)
    if best:
        return best[1]
    durations = [call for call in (_duration_ms(span) for span in spans) if call is not None]
    if not durations:
        return None
    return max(durations)


def _duration_ms(span: dict) -> int | None:
    start = parse_time(_as_time(_get(span, "start_time")))
    end = parse_time(_as_time(_get(span, "end_time")))
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _as_time(value) -> str | datetime | None:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    return str(value)
