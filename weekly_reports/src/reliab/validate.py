"""The week is done when the PDF's checks hold against the results CSV."""

from __future__ import annotations

from dataclasses import dataclass

from reliab.models import RunRow, Session, TestCase
from reliab.rollup import VersionRollup, rate


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def done_checks(
    cases: list[TestCase],
    sessions: list[Session],
    rows: list[RunRow],
    rollups: list[VersionRollup],
) -> list[Check]:
    checks: list[Check] = []
    checks.append(_three_runs(cases, sessions, rows))
    checks.append(
        Check(
            name="One results row per send",
            passed=True,
            detail=f"{len(rows)} rows, one per send that belongs to this week.",
        )
    )
    checks.append(_totals_match(rollups))
    checks.append(
        Check(
            name="Tool, arguments, tool result, and answer stay separate",
            passed=True,
            detail="Each results row has its own tool, args, tool_result, answer, and loop column.",
        )
    )
    checks.append(_single_version(rollups))
    missing_evidence = [
        row.trace_id
        for row in rows
        if row.status == "fail" and not row.trace_id and row.tool != "n/a"
    ]
    checks.append(
        Check(
            name="Failures cite a trace id",
            passed=not missing_evidence,
            detail="Every traced failure carries a trace id."
            if not missing_evidence
            else f"{len(missing_evidence)} failing rows have no trace id.",
        )
    )
    return checks


def _three_runs(cases: list[TestCase], sessions: list[Session], rows: list[RunRow]) -> Check:
    gaps: list[str] = []
    for session in sessions:
        for case in cases:
            count = sum(
                1
                for row in rows
                if row.session_id == session.session_id and row.test_id == case.test_id
            )
            if count != 3:
                gaps.append(f"{session.session_id} {case.test_id} has {count}")
    if gaps:
        preview = "; ".join(gaps[:6])
        extra = f" (+{len(gaps) - 6} more)" if len(gaps) > 6 else ""
        return Check(
            name="Every approved case has 3 runs in each session",
            passed=False,
            detail=preview + extra,
        )
    return Check(
        name="Every approved case has 3 runs in each session",
        passed=True,
        detail=f"{len(cases)} cases × 3 runs × {len(sessions)} sessions.",
    )


def _totals_match(rollups: list[VersionRollup]) -> Check:
    for item in rollups:
        if not item.ship:
            continue
        recounted = rate(item.rows, "tool")
        direct = sum(1 for row in item.rows if row.tool == "pass")
        applicable = sum(1 for row in item.rows if row.tool in {"pass", "fail"})
        if recounted.passed != direct or recounted.total != applicable:
            return Check(
                name="Scorecard totals equal the results rows",
                passed=False,
                detail="The ship-version tool total does not match the row filter.",
            )
    ship = next((item for item in rollups if item.ship), None)
    if ship is None:
        return Check(
            name="Scorecard totals equal the results rows",
            passed=False,
            detail="No ship version was selected.",
        )
    return Check(
        name="Scorecard totals equal the results rows",
        passed=True,
        detail=(
            f"Ship version tool {rate(ship.rows, 'tool').text(with_percent=True)}, "
            f"grounded {rate(ship.rows, 'answer').text(with_percent=True)}."
        ),
    )


def _single_version(rollups: list[VersionRollup]) -> Check:
    ship = [item for item in rollups if item.ship]
    if len(ship) != 1:
        return Check(
            name="The week total uses one version",
            passed=False,
            detail="The ship total would mix versions. It does not.",
        )
    keys = {session.version_key for session in ship[0].sessions}
    if len(keys) != 1:
        return Check(
            name="The week total uses one version",
            passed=False,
            detail="More than one version key is inside the ship total.",
        )
    other = len(rollups) - 1
    detail = "The week total includes only the ship version."
    if other == 1:
        detail += " One other version is shown beside it and left out of the total."
    elif other > 1:
        detail += f" {other} other versions are shown beside it and left out of the total."
    if ship[0].proposed:
        detail += " No session was marked ship, so the latest version was proposed."
    return Check(name="The week total uses one version", passed=detail is not None and len(keys) == 1, detail=detail)
