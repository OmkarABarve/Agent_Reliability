import unittest
from datetime import datetime, timezone

from reliab.models import Send, Session, TestCase, ToolCall, TraceBundle
from reliab.score import score_run

UTC = timezone.utc


def case(**overrides) -> TestCase:
    raw = dict(
        test_id="T-014",
        category="availability",
        question="Any vets open tomorrow at 5pm?",
        expected_tool="findAvailableDoctors",
        allowed_tools=("findAvailableDoctors",),
        forbidden_tools=("searchServiceProviders",),
        must_include_args=(("date", "tomorrow"),),
        expected_outcome="Lists only real vets with a free slot",
        needs_tool=True,
        result_expects=(("ok", "true"),),
        score_from_reply=False,
    )
    raw.update(overrides)
    return TestCase(**raw)


def session() -> Session:
    return Session(
        session_id="20261017-1430-baseline",
        started_at=datetime(2026, 10, 17, 14, 30, tzinfo=UTC),
        reason="baseline",
        model="gpt-4.1-mini",
        prompt_version="p14",
        step_count=8,
        agent_version="petto-2.3",
        ship=False,
        project="sharkk-petto-staging",
    )


def send(reply: str) -> Send:
    return Send(
        session_id="20261017-1430-baseline",
        test_id="T-014",
        run="r1",
        sent_at=datetime(2026, 10, 17, 14, 32, tzinfo=UTC),
        reply=reply,
        trace_id="tr-1",
    )


def trace(tools: list[ToolCall], answer: str = "") -> TraceBundle:
    return TraceBundle(
        trace_id="tr-1",
        session_id="20261017-1430-baseline",
        test_id="T-014",
        run="r1",
        tools=tools,
        answer=answer,
        latency_ms=2100,
        metadata={},
    )


def tool(name, args, result) -> ToolCall:
    return ToolCall(name=name, args=args, result=result, span_id=name)


class ScoreTests(unittest.TestCase):
    def test_extra_fields_do_not_fail_args(self):
        row = score_run(
            case(),
            session(),
            send("Dr. Adler is free tomorrow at 5pm."),
            trace(
                [
                    tool(
                        "findAvailableDoctors",
                        {"date": "tomorrow", "lat": 48.1, "lng": 11.5},
                        {"ok": True, "vets": [{"name": "Dr. Adler"}]},
                    )
                ]
            ),
        )
        self.assertEqual(row.tool, "pass")
        self.assertEqual(row.args, "pass")
        self.assertEqual(row.tool_result, "pass")
        self.assertEqual(row.answer, "pass")
        self.assertEqual(row.status, "pass")

    def test_forbidden_tool_fails_even_if_expected_tool_also_ran(self):
        row = score_run(
            case(),
            session(),
            send("Lakeside Animal Hospital is open tomorrow."),
            trace(
                [
                    tool("searchServiceProviders", {}, {"ok": False}),
                    tool(
                        "findAvailableDoctors",
                        {"date": "tomorrow"},
                        {"ok": True, "vets": [{"name": "Dr. Adler"}]},
                    ),
                ]
            ),
        )
        self.assertEqual(row.tool, "fail")
        self.assertEqual(row.primary_reason, "forbidden_tool")

    def test_skipped_tool_is_not_an_args_failure(self):
        row = score_run(
            case(),
            session(),
            send("Yes, Dr. Weber is open tomorrow at 5."),
            trace([]),
        )
        self.assertEqual(row.tool, "fail")
        self.assertEqual(row.args, "n/a")
        self.assertEqual(row.tool_result, "n/a")
        self.assertEqual(row.primary_reason, "skipped_tool")
        self.assertIn("ungrounded", row.reasons)

    def test_wrong_arg_fails_and_extra_absence_of_lat_does_not(self):
        row = score_run(
            case(),
            session(),
            send("Dr. Adler is free tomorrow at 5pm."),
            trace(
                [
                    tool(
                        "findAvailableDoctors",
                        {"date": "today"},
                        {"ok": True, "vets": [{"name": "Dr. Adler"}]},
                    )
                ]
            ),
        )
        self.assertEqual(row.tool, "pass")
        self.assertEqual(row.args, "fail")
        self.assertEqual(row.primary_reason, "bad_args")

    def test_booking_result_must_match_confirmation(self):
        booking = case(
            test_id="T-031",
            category="booking",
            question="Book Mika for Tuesday at 5pm.",
            expected_tool="bookAppointment",
            allowed_tools=("bookAppointment",),
            forbidden_tools=(),
            must_include_args=(("pet", "Mika"),),
            expected_outcome="Asks the owner to confirm before the visit is final.",
            result_expects=(("ok", "true"), ("needsConfirmation", "true")),
        )
        row = score_run(
            booking,
            session(),
            send("Dr. Adler is booked for Mika on Tuesday at 5pm."),
            trace(
                [
                    tool(
                        "bookAppointment",
                        {"pet": "Mika"},
                        {"ok": True, "needsConfirmation": False, "booked": True, "vet": "Dr. Adler", "pet": "Mika"},
                    )
                ]
            ),
        )
        self.assertEqual(row.tool_result, "fail")
        self.assertEqual(row.answer, "pass")
        self.assertEqual(row.primary_reason, "bad_tool_result")

    def test_three_calls_is_a_loop_two_is_not(self):
        calls = [
            tool("getPetProfile", {"pet": "Mika"}, {"ok": True, "name": "Mika", "species": "dog"})
            for _ in range(3)
        ]
        profile = case(
            test_id="T-050",
            category="profile",
            question="What do you have on file for Mika?",
            expected_tool="getPetProfile",
            allowed_tools=("getPetProfile",),
            forbidden_tools=(),
            must_include_args=(("pet", "Mika"),),
            expected_outcome="Describes only the pet returned by the tool.",
        )
        looped = score_run(profile, session(), send("Mika is a dog on file."), trace(calls))
        self.assertEqual(looped.loop, "fail")
        self.assertEqual(looped.tool, "pass")
        once = score_run(profile, session(), send("Mika is a dog on file."), trace(calls[:2]))
        self.assertEqual(once.loop, "pass")
        self.assertEqual(once.status, "pass")

    def test_missing_trace_is_untraced_unless_the_case_is_scored_from_the_reply(self):
        missing = score_run(case(), session(), send("Dr. Adler is free tomorrow at 5pm."), None)
        self.assertEqual(missing.status, "untraced")
        self.assertEqual(missing.tool, "untraced")
        from_reply = score_run(
            case(
                needs_tool=False,
                expected_tool="",
                allowed_tools=(),
                forbidden_tools=(),
                must_include_args=(),
                expected_outcome="Greets the owner and waits.",
                score_from_reply=True,
            ),
            session(),
            send("Hello. I can help with vets, pets, and appointments."),
            None,
        )
        self.assertEqual(from_reply.tool, "n/a")
        self.assertEqual(from_reply.status, "pass")

    def test_greeting_that_calls_a_tool_fails_necessity(self):
        greeting = case(
            test_id="T-001",
            category="greeting",
            question="Hello",
            expected_tool="",
            allowed_tools=(),
            forbidden_tools=(),
            must_include_args=(),
            expected_outcome="Greets the owner and waits.",
            needs_tool=False,
        )
        row = score_run(
            greeting,
            session(),
            send("Hello. I can help with vets, pets, and appointments."),
            trace([tool("getPetProfile", {}, {"ok": True, "name": "Mika"})]),
        )
        self.assertEqual(row.tool, "fail")
        self.assertEqual(row.primary_reason, "unnecessary_tool")


if __name__ == "__main__":
    unittest.main()
