"""Pull spans out of Phoenix for one ISO week.

The Phoenix UI caps a view at 1,000 spans, and a week of test sessions can pass
that. This export asks for each day on its own, then stacks and de-duplicates
by span id. One project per client and environment is the intended layout.
A new project every day makes this join painful; pass session_id on the span
instead.
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

from reliab.models import week_datetimes


def export_week(project: str, week: str, out: Path, endpoint: str | None = None, api_key: str | None = None) -> int:
    base = endpoint or os.environ.get("PHOENIX_ENDPOINT") or "http://127.0.0.1:6006"
    key = api_key if api_key is not None else os.environ.get("PHOENIX_API_KEY") or None
    start, end = week_datetimes(week)
    seen: set[str] = set()
    records: list[dict] = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + timedelta(days=1), end)
        frame = _pull(base, key, project, cursor, nxt)
        rows = _frame_to_records(frame)
        if len(rows) >= 1000:
            print(
                f"warning: {cursor.date()} returned {len(rows)} spans. "
                "If that is the server cap, split the day before trusting the export."
            )
        for row in rows:
            span_id = str(row.get("context.span_id") or row.get("span_id") or "")
            if span_id and span_id in seen:
                continue
            if span_id:
                seen.add(span_id)
            records.append(row)
        cursor = nxt
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, default=str) + "\n")
    return len(records)


def _pull(endpoint: str, api_key: str | None, project: str, start, end):
    errors: list[str] = []
    try:
        from phoenix.client import Client

        kwargs = {"base_url": endpoint}
        if api_key:
            kwargs["api_key"] = api_key
        client = Client(**kwargs)
        return client.spans.get_spans_dataframe(
            project_identifier=project,
            start_time=start,
            end_time=end,
        )
    except Exception as exc:  # ImportError or a client/API mismatch
        errors.append(f"phoenix.client: {exc}")
    try:
        import phoenix as px

        client = px.Client(endpoint=endpoint)
        return client.get_spans_dataframe(project_name=project, start_time=start, end_time=end)
    except Exception as exc:
        errors.append(f"phoenix.Client: {exc}")
    raise RuntimeError(
        "Could not export spans from Phoenix. Install arize-phoenix-client, or pass a spans JSONL "
        "you already downloaded. "
        + " | ".join(errors)
    )


def _frame_to_records(frame) -> list[dict]:
    if frame is None:
        return []
    columns = getattr(frame, "columns", None)
    if columns is not None and getattr(columns, "nlevels", 1) > 1:
        frame = frame.copy()
        frame.columns = [
            ".".join(str(part) for part in column if str(part) not in {"", "None"})
            for column in frame.columns
        ]
    if hasattr(frame, "to_dict"):
        return frame.to_dict(orient="records")
    return list(frame)
