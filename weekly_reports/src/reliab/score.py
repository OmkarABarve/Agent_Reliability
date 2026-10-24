"""Score one run. The columns stay separate so a wrong tool is not blamed on the wording.

Order for the tool column:
  1. A forbidden tool is an instant fail.
  2. needs_tool and an empty list is a fail.
  3. A tool call when the case needs none is a fail.
  4. The expected tool has to appear at least once.
  5. A tool outside the allowed list is a fail.

Args are n/a when the expected tool never ran. Extra fields such as lat or lng
do not fail the args column. The same tool three or more times fails the loop
column even when it was the expected tool.
"""

from __future__ import annotations

import json

from reliab.judge import RuleJudge
from reliab.models import RunRow, Send, Session, TestCase, TraceBundle

PRIORITY = (
    "untraced",
    "forbidden_tool",
    "skipped_tool",
    "missing_expected_tool",
    "disallowed_tool",
    "unnecessary_tool",
    "bad_args",
    "bad_tool_result",
    "loop",
    "ungrounded",
    "empty_reply",
)


def score_run(
    case: TestCase,
    session: Session,
    send: Send,
    trace: TraceBundle | None,
    judge: RuleJudge | None = None,
) -> RunRow:
    judge = judge or RuleJudge()
    reply = send.reply if send.reply else (trace.answer if trace else "")
    reasons: list[str] = []

    if trace is None and not case.score_from_reply:
        tool = args = tool_result = answer = loop = "untraced"
        reasons.append("untraced")
        status = "untraced"
        tools_called = ""
        result_text = ""
        milliseconds = None
        trace_id = send.trace_id
    elif trace is None and case.score_from_reply:
        tool = args = tool_result = loop = "n/a"
        answer, answer_reason = judge.judge(case, reply, [], case.question)
        if answer_reason:
            reasons.append(answer_reason)
        tools_called = ""
        result_text = ""
        milliseconds = None
        trace_id = ""
        status = "fail" if answer == "fail" else "pass"
    else:
        assert trace is not None
        names = [call.name for call in trace.tools]
        tools_called = ", ".join(names)
        tool, tool_reason = _score_tool(case, names)
        expected_calls = [call for call in trace.tools if call.name.casefold() == case.expected_tool.casefold()]
        if tool_reason:
            reasons.append(tool_reason)
        if expected_calls:
            args, args_reason = _score_args(case, expected_calls[-1].args)
            tool_result, result_reason = _score_result(case, expected_calls[-1].result)
            result_text = _short(expected_calls[-1].result)
        else:
            args, args_reason = "n/a", ""
            tool_result, result_reason = "n/a", ""
            result_text = ""
        if args_reason:
            reasons.append(args_reason)
        if result_reason:
            reasons.append(result_reason)
        loop, loop_reason = _score_loop(names)
        if loop_reason:
            reasons.append(loop_reason)
        answer, answer_reason = judge.judge(
            case,
            reply,
            [call.result for call in trace.tools],
            case.question,
        )
        if answer_reason:
            reasons.append(answer_reason)
        status = "fail" if any(
            column == "fail" for column in (tool, args, tool_result, answer, loop)
        ) else "pass"
        milliseconds = trace.latency_ms
        trace_id = trace.trace_id or send.trace_id

    primary = next((code for code in PRIORITY if code in reasons), "")
    return RunRow(
        session_id=session.session_id,
        session_reason=session.reason,
        session_started=session.started_at,
        test_id=case.test_id,
        run=send.run,
        category=case.category,
        question=case.question,
        expected_tool=case.expected_tool,
        tools_called=tools_called,
        tool=tool,
        args=args,
        tool_result=tool_result,
        answer=answer,
        loop=loop,
        status=status,
        ms=milliseconds,
        trace_id=trace_id,
        reasons=tuple(reasons),
        primary_reason=primary,
        version_key=session.version_key,
        agent_version=session.agent_version,
        prompt_version=session.prompt_version,
        model=session.model,
        step_count=session.step_count,
        ship=session.ship,
        reply=reply,
        tool_result_text=result_text,
    )


def _score_tool(case: TestCase, names: list[str]) -> tuple[str, str]:
    folded = {name.casefold() for name in names}
    forbidden = {name.casefold() for name in case.forbidden_tools}
    if folded & forbidden:
        return "fail", "forbidden_tool"
    if case.needs_tool and not names:
        return "fail", "skipped_tool"
    if not case.needs_tool and names:
        return "fail", "unnecessary_tool"
    if case.expected_tool and case.expected_tool.casefold() not in folded:
        return "fail", "missing_expected_tool"
    allowed = {name.casefold() for name in case.effective_allowed}
    if allowed and "*" not in allowed:
        if any(name.casefold() not in allowed for name in names):
            return "fail", "disallowed_tool"
    return "pass", ""


def _score_args(case: TestCase, args: object) -> tuple[str, str]:
    if not case.must_include_args:
        return "pass", ""
    for key, expected in case.must_include_args:
        values = _find_values(args, key)
        if not values:
            return "fail", "bad_args"
        if not any(_matches(value, expected) for value in values):
            return "fail", "bad_args"
    return "pass", ""


def _score_result(case: TestCase, result: object) -> tuple[str, str]:
    payload = result if isinstance(result, dict) else {}
    expects = case.result_expects or (("ok", "true"),)
    for key, expected in expects:
        if key not in payload:
            return "fail", "bad_tool_result"
        if not _matches(payload[key], expected):
            return "fail", "bad_tool_result"
    return "pass", ""


def _score_loop(names: list[str]) -> tuple[str, str]:
    counts: dict[str, int] = {}
    for name in names:
        counts[name.casefold()] = counts.get(name.casefold(), 0) + 1
    if any(count >= 3 for count in counts.values()):
        return "fail", "loop"
    return "pass", ""


def _find_values(payload: object, key: str) -> list:
    found: list = []
    if isinstance(payload, dict):
        for name, value in payload.items():
            if str(name).casefold() == key.casefold():
                found.append(value)
            found.extend(_find_values(value, key))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_find_values(item, key))
    return found


def _matches(actual, expected: str) -> bool:
    if isinstance(actual, bool):
        return ("true" if actual else "false") == expected.strip().casefold()
    if isinstance(actual, (int, float)) and not isinstance(actual, bool):
        return str(actual).strip().casefold() == expected.strip().casefold()
    return str(actual).strip().casefold() == expected.strip().casefold()


def _short(result: object, limit: int = 280) -> str:
    try:
        text = json.dumps(result, ensure_ascii=False)
    except TypeError:
        text = str(result)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
