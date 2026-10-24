"""Command line for the weekly report."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reliab.demo_data import write_inputs
from reliab.load import load_cases, load_engagement, load_sends, load_sessions, load_spans
from reliab.phoenix_export import export_week
from reliab.pipeline import run_week


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="reliab", description="Weekly agent reliability reports from Phoenix traces.")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="Write the Sharkk example week and render its report.")
    demo.add_argument("--out", default="out/2026-W42", help="Directory for the report files.")
    demo.add_argument("--inputs", default="examples/sharkk", help="Directory for the example inputs.")

    weekly = sub.add_parser("weekly", help="Score a week from local files and write the report.")
    weekly.add_argument("--engagement", required=True)
    weekly.add_argument("--cases", required=True)
    weekly.add_argument("--sessions", required=True)
    weekly.add_argument("--send-log", required=True)
    weekly.add_argument("--spans", required=True)
    weekly.add_argument("--out", required=True)
    weekly.add_argument("--strict", action="store_true", help="Exit non-zero when a done-when check fails.")

    export = sub.add_parser("export-phoenix", help="Download one ISO week of spans, one day at a time.")
    export.add_argument("--project", required=True)
    export.add_argument("--week", required=True)
    export.add_argument("--out", required=True)
    export.add_argument("--endpoint", default=None)

    args = parser.parse_args(argv)
    if args.command == "demo":
        inputs = Path(args.inputs)
        write_inputs(inputs)
        view = _run_from_dir(inputs, Path(args.out))
        _print_summary(view, Path(args.out))
        return
    if args.command == "weekly":
        view = run_week(
            load_engagement(Path(args.engagement)),
            load_cases(Path(args.cases)),
            load_sessions(Path(args.sessions)),
            load_sends(Path(args.send_log)),
            load_spans(Path(args.spans)),
            Path(args.out),
        )
        _print_summary(view, Path(args.out))
        if args.strict and any(not check.passed for check in view.checks):
            sys.exit(1)
        return
    count = export_week(args.project, args.week, Path(args.out), endpoint=args.endpoint)
    print(f"Wrote {count} spans to {args.out}")


def _run_from_dir(inputs: Path, out: Path):
    return run_week(
        load_engagement(inputs / "engagement.json"),
        load_cases(inputs / "test_cases.csv"),
        load_sessions(inputs / "sessions.json"),
        load_sends(inputs / "send_log.jsonl"),
        load_spans(inputs / "spans.jsonl"),
        out,
    )


def _print_summary(view, out: Path) -> None:
    ship = next(item for item in view.rollups if item.ship)
    print(f"{view.engagement.client} {view.engagement.week}: {view.verdict}")
    print(f"Ship version {ship.version_key} scores {ship.score}/100 on {len(ship.rows)} runs.")
    print(f"Findings: {len(view.findings)}. Review queue: {len(view.review)}.")
    for check in view.checks:
        mark = "pass" if check.passed else "FAIL"
        print(f"  [{mark}] {check.name}")
    for warning in view.warnings:
        print(f"  note: {warning}")
    print(f"Report: {out / 'report.html'}")


if __name__ == "__main__":
    main()
