import tempfile
import unittest
from pathlib import Path

from reliab.demo_data import build_week, write_inputs
from reliab.load import load_cases, load_engagement, load_sends, load_sessions, load_spans
from reliab.pipeline import run_week
from reliab.rollup import rate


class WeekTests(unittest.TestCase):
    def test_ship_total_excludes_the_other_prompt(self):
        engagement, cases, sessions, sends, spans = build_week()
        with tempfile.TemporaryDirectory() as directory:
            view = run_week(engagement, cases, sessions, sends, spans, Path(directory))
        ship = next(item for item in view.rollups if item.ship)
        other = next(item for item in view.rollups if not item.ship)
        self.assertEqual(ship.sessions[0].prompt_version, "p15")
        self.assertEqual(len(ship.rows), 24)
        self.assertEqual(len(other.rows), 24)
        self.assertNotEqual(ship.version_key, other.version_key)
        self.assertEqual(rate(ship.rows, "tool").text(), "24/24")
        self.assertLess(rate(other.rows, "tool").fraction, 1)
        reasons = {finding.reason for finding in view.findings}
        self.assertIn("bad_tool_result", reasons)
        self.assertIn("loop", reasons)
        self.assertNotIn("skipped_tool", reasons)
        self.assertEqual(view.verdict, "Not ready")
        self.assertTrue(all(check.passed for check in view.checks))
        fixed = {change.test_id for change in view.changes if change.kind == "fixed"}
        self.assertIn("T-014", fixed)
        self.assertIn("T-018", fixed)
        persisted = {change.test_id for change in view.changes if change.kind == "persisting"}
        self.assertIn("T-031", persisted)

    def test_file_round_trip_matches_the_in_memory_week(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_inputs(root / "inputs")
            engagement, cases, sessions, sends, spans = build_week()
            direct = run_week(engagement, cases, sessions, sends, spans, root / "direct")
            loaded = run_week(
                load_engagement(root / "inputs" / "engagement.json"),
                load_cases(root / "inputs" / "test_cases.csv"),
                load_sessions(root / "inputs" / "sessions.json"),
                load_sends(root / "inputs" / "send_log.jsonl"),
                load_spans(root / "inputs" / "spans.jsonl"),
                root / "loaded",
            )
            self.assertEqual(
                [(row.test_id, row.run, row.status, row.primary_reason) for row in direct.rows],
                [(row.test_id, row.run, row.status, row.primary_reason) for row in loaded.rows],
            )
            self.assertTrue((root / "loaded" / "report.html").exists())
            self.assertTrue((root / "loaded" / "results.csv").exists())


if __name__ == "__main__":
    unittest.main()
