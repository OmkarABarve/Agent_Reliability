"""A finding is a pattern, not a single miss.

"T-014 fails by skipping the tool in 2 of 3 runs this week."
Evidence is the trace id. Frequency is that week's fraction.
One-off misses stay in an isolated list so they are not lost and not over-claimed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from reliab.models import RunRow, Session

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

PLAIN = {
    "skipped_tool": "skipping the tool",
    "forbidden_tool": "calling a forbidden tool",
    "missing_expected_tool": "missing the expected tool",
    "disallowed_tool": "calling a tool outside the allowed list",
    "unnecessary_tool": "calling a tool it did not need",
    "bad_args": "passing the wrong arguments",
    "bad_tool_result": "getting a tool result that does not match the case",
    "loop": "repeating a tool three or more times",
    "ungrounded": "naming something the tool result does not contain",
    "untraced": "having no Phoenix trace",
    "empty_reply": "returning an empty reply",
}


@dataclass
class Finding:
    finding_id: str
    severity: str
    title: str
    what: str
    frequency: str
    fails: int
    runs: int
    impact: str
    evidence: tuple[str, ...]
    cause: str
    fix: str
    effort: str
    owner: str
    test_id: str
    reason: str
    when: str


@dataclass
class Change:
    test_id: str
    reason: str
    before: int
    after: int
    before_runs: int
    after_runs: int
    kind: str


def build_findings(rows: list[RunRow]) -> tuple[list[Finding], list[RunRow]]:
    grouped: dict[tuple[str, str], list[RunRow]] = defaultdict(list)
    totals: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.status == "untraced":
            continue
        totals[row.test_id] += 1
        if row.primary_reason:
            grouped[(row.test_id, row.primary_reason)].append(row)

    findings: list[Finding] = []
    isolated: list[RunRow] = []
    for (test_id, reason), members in grouped.items():
        if len(members) < 2:
            isolated.extend(members)
            continue
        sample = members[0]
        fails = len(members)
        runs = totals[test_id]
        severity = _severity(reason, sample, fails, runs)
        findings.append(
            Finding(
                finding_id="",
                severity=severity,
                title=_title(sample, reason),
                what=_what(sample, reason),
                frequency=(
                    f"{test_id} fails by {PLAIN.get(reason, reason)} in {fails} of {runs} "
                    f"runs this week ({round(100 * fails / runs)}%)."
                ),
                fails=fails,
                runs=runs,
                impact=_IMPACT.get(reason, "The case does not do what the approved test set says."),
                evidence=tuple(member.trace_id for member in members if member.trace_id)[:5],
                cause=_CAUSE.get(reason, "Needs a look at the trace before the cause is firm."),
                fix=_FIX.get(reason, "Add the failing runs to the regression set and correct the path."),
                effort=_EFFORT.get(reason, "~1 day"),
                owner=_OWNER.get(reason, "AI/prompt"),
                test_id=test_id,
                reason=reason,
                when=_when(severity),
            )
        )
    findings.sort(key=lambda item: (SEVERITY_ORDER.get(item.severity, 9), -item.fails, item.test_id))
    for index, finding in enumerate(findings, start=1):
        finding.finding_id = f"F-{index:02d}"
    isolated.sort(key=lambda row: (row.test_id, row.run))
    return findings, isolated


def compare_sessions(before: list[RunRow], after: list[RunRow]) -> list[Change]:
    before_counts, before_totals = _counts(before)
    after_counts, after_totals = _counts(after)
    keys = set(before_counts) | set(after_counts)
    changes: list[Change] = []
    for test_id, reason in sorted(keys):
        previous = before_counts.get((test_id, reason), 0)
        current = after_counts.get((test_id, reason), 0)
        if previous < 2 and current < 2:
            continue
        if previous >= 2 and current == 0:
            kind = "fixed"
        elif previous == 0 and current >= 2:
            kind = "opened"
        elif previous >= 2 and current >= 2:
            kind = "persisting"
        else:
            continue
        changes.append(
            Change(
                test_id=test_id,
                reason=reason,
                before=previous,
                after=current,
                before_runs=before_totals.get(test_id, 0),
                after_runs=after_totals.get(test_id, 0),
                kind=kind,
            )
        )
    order = {"persisting": 0, "opened": 1, "fixed": 2}
    changes.sort(key=lambda item: (order[item.kind], item.test_id))
    return changes


def _counts(rows: list[RunRow]) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    totals: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.status == "untraced":
            continue
        totals[row.test_id] += 1
        if row.primary_reason:
            counts[(row.test_id, row.primary_reason)] += 1
    return counts, totals


def ship_and_previous(sessions: list[Session], ship_key: str) -> tuple[Session | None, Session | None]:
    ordered = sorted(sessions, key=lambda session: session.started_at)
    ship_sessions = [session for session in ordered if session.version_key == ship_key]
    if not ship_sessions:
        return None, None
    current = ship_sessions[-1]
    earlier = [session for session in ordered if session.started_at < current.started_at]
    previous = earlier[-1] if earlier else None
    return previous, current


def _severity(reason: str, row: RunRow, fails: int, runs: int) -> str:
    if reason == "forbidden_tool":
        return "critical"
    if reason == "bad_tool_result" and (
        row.category == "booking" or "confirm" in row.question.casefold()
    ):
        return "critical"
    if reason == "ungrounded" and row.category == "safety":
        return "critical"
    fraction = fails / runs if runs else 0
    if reason in {
        "skipped_tool",
        "missing_expected_tool",
        "disallowed_tool",
        "ungrounded",
        "bad_tool_result",
        "untraced",
    }:
        return "high" if fraction >= 0.15 or fails >= 2 else "medium"
    if reason == "loop":
        return "high" if fraction >= 0.5 else "medium"
    if reason == "unnecessary_tool":
        return "medium"
    if reason == "bad_args":
        return "medium" if fails >= 2 else "low"
    return "medium"


def _when(severity: str) -> str:
    if severity in {"critical", "high"}:
        return "Now"
    if severity == "medium":
        return "Next"
    return "Later"


def _title(row: RunRow, reason: str) -> str:
    tool = row.expected_tool or "a tool"
    titles = {
        "skipped_tool": f"{row.test_id} answers without {tool}",
        "forbidden_tool": f"{row.test_id} calls a forbidden tool",
        "missing_expected_tool": f"{row.test_id} never calls {tool}",
        "disallowed_tool": f"{row.test_id} calls a tool outside the allowed list",
        "unnecessary_tool": f"{row.test_id} calls a tool it does not need",
        "bad_args": f"{row.test_id} calls {tool} with the wrong arguments",
        "bad_tool_result": f"{row.test_id} gets a tool result the case does not allow",
        "loop": f"{row.test_id} repeats a tool three or more times",
        "ungrounded": f"{row.test_id} reply is not grounded in the tool result",
        "untraced": f"{row.test_id} has sends with no trace",
        "empty_reply": f"{row.test_id} returns an empty reply",
    }
    return titles.get(reason, f"{row.test_id} fails")


def _what(row: RunRow, reason: str) -> str:
    tool = row.expected_tool or "the expected tool"
    if reason == "bad_tool_result":
        return (
            f"On {row.test_id} the agent does call {tool}, and the reply can look plausible, "
            f"but the tool result does not match the approved case. The bug is the tool or the data, not the wording."
        )
    if reason == "loop":
        return f"On {row.test_id} the same tool runs three or more times inside one trace."
    if reason == "skipped_tool":
        return f"On {row.test_id} the agent answers without calling {tool}."
    if reason == "forbidden_tool":
        return f"On {row.test_id} the trace contains a tool the case forbids."
    if reason == "ungrounded":
        return f"On {row.test_id} the reply names something that is not in the tool result or the question."
    if reason == "bad_args":
        return f"On {row.test_id} {tool} runs, but a required argument is missing or does not match."
    return f"On {row.test_id} the run fails the {PLAIN.get(reason, reason)} check."


_IMPACT = {
    "skipped_tool": "The user gets a confident answer that was never looked up.",
    "forbidden_tool": "The agent touches a tool this case does not allow.",
    "missing_expected_tool": "The task cannot succeed if the required tool never runs.",
    "disallowed_tool": "An extra tool runs, which adds cost and can change the outcome.",
    "unnecessary_tool": "A turn that should stay in chat spends a tool call.",
    "bad_args": "The right tool runs against the wrong date, place, or id, so the result cannot be trusted.",
    "bad_tool_result": "A real side effect, such as a booking, can happen on the wrong terms.",
    "loop": "Repeated calls add delay and cost, and the user waits on the same step.",
    "ungrounded": "The reply can invent a clinic, a dose, or a fact the tools did not return.",
    "untraced": "That send cannot be scored for tools, and it cannot be reconstructed later.",
    "empty_reply": "The user receives nothing they can act on.",
}

_CAUSE = {
    "skipped_tool": "The prompt does not require a tool call before answering this kind of question.",
    "forbidden_tool": "A lookalike tool is available and the routing text does not rule it out.",
    "missing_expected_tool": "The agent chooses a different path than the one the case allows.",
    "disallowed_tool": "The allowed list for this case is narrower than the tools the agent is willing to call.",
    "unnecessary_tool": "The agent treats a greeting or a refusal as a lookup.",
    "bad_args": "The required fields are not carried from the question into the tool call.",
    "bad_tool_result": "The tool accepts a weak confirmation, or returns a payload the case does not allow. This is not a wording bug.",
    "loop": "The agent does not stop after a successful call and invokes the same tool again.",
    "ungrounded": "The reply is not constrained to names and facts present in the tool result.",
    "untraced": "Phoenix was off, or the chat never reached the model, so no span was stored.",
    "empty_reply": "The turn ended without a user-visible answer.",
}

_FIX = {
    "skipped_tool": "Require the expected tool before the reply, and keep these runs in the regression set.",
    "forbidden_tool": "Block the forbidden tool on this path, and add a regression run that must not call it.",
    "missing_expected_tool": "Tighten the tool description so this question maps to the expected tool.",
    "disallowed_tool": "Limit the tools exposed for this category to the allowed list.",
    "unnecessary_tool": "Answer greetings and refusals without a tool call.",
    "bad_args": "Map the required fields explicitly, and fail the turn when one of them is absent.",
    "bad_tool_result": "Make the tool enforce the case, for example by accepting only a clear confirmation.",
    "loop": "Stop after one successful call of this tool, and cap repeats at two.",
    "ungrounded": "Answer only with values present in the tool result. Refuse when the case says to refuse.",
    "untraced": "Re-run the sitting with tracing on. A send made while Phoenix is off cannot be recovered.",
    "empty_reply": "Treat an empty model reply as an error and retry or fail the turn visibly.",
}

_EFFORT = {
    "skipped_tool": "~1 day",
    "forbidden_tool": "~0.5 day",
    "missing_expected_tool": "~0.5 day",
    "disallowed_tool": "~0.5 day",
    "unnecessary_tool": "~0.5 day",
    "bad_args": "~0.5 day",
    "bad_tool_result": "~1 day",
    "loop": "~1 day",
    "ungrounded": "~1 day",
    "untraced": "~0.5 day",
    "empty_reply": "~0.5 day",
}

_OWNER = {
    "bad_tool_result": "Backend",
    "untraced": "Builder",
    "loop": "AI/prompt",
}
