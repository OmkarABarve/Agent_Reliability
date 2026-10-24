"""What the builder adds on the agent side.

One Phoenix project stays up for the whole engagement. Each chat carries
session_id, test_id, and run as trace metadata. The send log is the other
half of the join. Matching on question text is not used: two cases can share
a question.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc


def new_session_id(reason: str, when: datetime | None = None) -> str:
    """YYYYMMDD-hhmm plus a short reason. A new prompt, model, or step count is a new session."""
    moment = when or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    slug = re.sub(r"[^a-z0-9]+", "-", reason.strip().casefold()).strip("-") or "session"
    return f"{moment.strftime('%Y%m%d-%H%M')}-{slug}"


def stamp_span(
    span,
    *,
    session_id: str,
    test_id: str,
    run: str,
    agent_version: str,
    prompt_version: str,
    step_count: int,
    model: str,
) -> None:
    """Write the join keys onto the current OpenTelemetry span. Behavior of the agent is unchanged."""
    metadata = {
        "session_id": session_id,
        "test_id": test_id,
        "run": run,
        "agent_version": agent_version,
        "prompt_version": prompt_version,
        "step_count": step_count,
        "model": model,
    }
    span.set_attribute("session.id", session_id)
    span.set_attribute("metadata", json.dumps(metadata))


class SendLogger:
    """Append-only log of every send. This file is the join key for the week."""

    def __init__(self, path: Path, session_id: str):
        self.path = Path(path)
        self.session_id = session_id
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        test_id: str,
        run: str,
        reply: str,
        sent_at: datetime | None = None,
        trace_id: str = "",
        question: str = "",
    ) -> dict:
        moment = sent_at or datetime.now(UTC)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        row = {
            "session_id": self.session_id,
            "test_id": test_id,
            "run": run,
            "sent_at": moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reply": reply,
            "trace_id": trace_id,
            "question": question,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row
