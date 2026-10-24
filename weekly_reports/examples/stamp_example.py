"""How a chat handler stamps the join keys. The agent's answers do not change.

Keep one Phoenix project for the engagement. Pass session_id, test_id, and run
on the span. Append the same keys, plus the reply, to the send log.
"""

from __future__ import annotations

import json

from reliab.instrument import SendLogger, new_session_id, stamp_span


class CurrentSpan:
    """Stand-in for the active OpenTelemetry span."""

    def __init__(self):
        self.attributes = {}

    def set_attribute(self, key, value):
        self.attributes[key] = value


def handle_test_turn(logger: SendLogger, span: CurrentSpan, test_id: str, run: str, question: str, reply: str, trace_id: str):
    stamp_span(
        span,
        session_id=logger.session_id,
        test_id=test_id,
        run=run,
        agent_version="petto-2.3",
        prompt_version="p15",
        step_count=8,
        model="gpt-4.1-mini",
    )
    logger.record(test_id=test_id, run=run, reply=reply, trace_id=trace_id, question=question)
    return span.attributes


if __name__ == "__main__":
    session_id = new_session_id("after-prompt-fix")
    print(session_id)
    print("metadata on the span:")
    logged = handle_test_turn(
        SendLogger("out/send_log.jsonl", session_id),
        CurrentSpan(),
        "T-014",
        "r1",
        "Any vets open tomorrow at 5pm?",
        "Dr. Adler is free tomorrow at 5pm.",
        "tr-example",
    )
    print(json.dumps(logged, indent=2))
