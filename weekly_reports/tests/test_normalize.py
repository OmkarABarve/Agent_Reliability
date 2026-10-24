import json
import unittest

from reliab.normalize import is_tool, normalize_spans


class NormalizeTests(unittest.TestCase):
    def test_metadata_on_the_root_span_reaches_the_trace(self):
        metadata = {"session_id": "20261017-1430-baseline", "test_id": "T-014", "run": "r2"}
        spans = [
            {
                "name": "petto-chat",
                "span_kind": "AGENT",
                "parent_id": None,
                "start_time": "2026-10-17T14:32:00Z",
                "end_time": "2026-10-17T14:32:02.100Z",
                "context.trace_id": "tr-1",
                "context.span_id": "agent",
                "attributes.output.value": "Dr. Adler is free tomorrow at 5pm.",
                "attributes.metadata": json.dumps(metadata),
                "attributes.session.id": metadata["session_id"],
            },
            {
                "name": "findAvailableDoctors",
                "span_kind": "TOOL",
                "parent_id": "agent",
                "start_time": "2026-10-17T14:32:00.100Z",
                "end_time": "2026-10-17T14:32:00.400Z",
                "context.trace_id": "tr-1",
                "context.span_id": "tool",
                "attributes.tool.name": "findAvailableDoctors",
                "attributes.input.value": json.dumps({"date": "tomorrow", "lat": 1}),
                "attributes.output.value": json.dumps({"ok": True, "vets": [{"name": "Dr. Adler"}]}),
            },
        ]
        self.assertTrue(is_tool(spans[1]))
        self.assertFalse(is_tool(spans[0]))
        bundles = normalize_spans(spans)
        self.assertEqual(len(bundles), 1)
        bundle = bundles[0]
        self.assertEqual(bundle.session_id, "20261017-1430-baseline")
        self.assertEqual(bundle.test_id, "T-014")
        self.assertEqual(bundle.run, "r2")
        self.assertEqual(bundle.tools[0].name, "findAvailableDoctors")
        self.assertEqual(bundle.tools[0].args["date"], "tomorrow")
        self.assertEqual(bundle.answer, "Dr. Adler is free tomorrow at 5pm.")
        self.assertEqual(bundle.latency_ms, 2100)

    def test_tool_order_follows_start_time(self):
        spans = []
        for index, name in enumerate(("bookAppointment", "searchDoctorsClinics"), start=1):
            spans.append(
                {
                    "name": name,
                    "span_kind": "TOOL",
                    "start_time": f"2026-10-17T14:32:0{3 - index}Z",
                    "end_time": f"2026-10-17T14:32:0{4 - index}Z",
                    "context.trace_id": "tr-2",
                    "context.span_id": f"s{index}",
                    "attributes.tool.name": name,
                    "attributes.input.value": "{}",
                    "attributes.output.value": json.dumps({"ok": True}),
                }
            )
        # bookAppointment was given the later timestamp via 3-index, so search runs first.
        names = [call.name for call in normalize_spans(spans)[0].tools]
        self.assertEqual(names, ["searchDoctorsClinics", "bookAppointment"])


if __name__ == "__main__":
    unittest.main()
