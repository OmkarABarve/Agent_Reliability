"""Session columns, and a week total only for the version you would ship.

A change of model, prompt version, or step count is a different version.
Those sessions stay in their own columns and are left out of the week total.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from reliab.load import DEFAULT_WEIGHTS
from reliab.models import Engagement, RunRow, Session

DIMENSIONS = ("tool", "args", "tool_result", "answer", "loop")


@dataclass(frozen=True)
class Rate:
    passed: int
    total: int

    @property
    def fraction(self) -> float | None:
        if self.total == 0:
            return None
        return self.passed / self.total

    def text(self, with_percent: bool = False) -> str:
        if self.total == 0:
            return "n/a"
        if with_percent:
            percent = round(100 * self.passed / self.total)
            return f"{self.passed}/{self.total} = {percent}%"
        return f"{self.passed}/{self.total}"


@dataclass
class VersionRollup:
    version_key: str
    ship: bool
    proposed: bool
    sessions: list[Session]
    rows: list[RunRow]
    p95_ms: int | None
    score: int | None
    consistency: Rate
    category_tool: dict[str, Rate] = field(default_factory=dict)


def rate(rows: list[RunRow], dimension: str) -> Rate:
    applicable = [row for row in rows if row.dimension(dimension) in {"pass", "fail"}]
    passed = sum(1 for row in applicable if row.dimension(dimension) == "pass")
    return Rate(passed, len(applicable))


def percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = math.ceil(fraction * len(ordered)) - 1
    index = min(max(index, 0), len(ordered) - 1)
    return ordered[index]


def choose_ship_version(sessions: list[Session]) -> tuple[str, bool]:
    """Return (version_key, proposed). proposed is true when no session was marked ship."""
    if not sessions:
        return "", True
    marked = [session for session in sessions if session.ship]
    if not marked:
        latest = max(sessions, key=lambda session: session.started_at)
        return latest.version_key, True
    versions = {session.version_key for session in marked}
    if len(versions) == 1:
        return marked[0].version_key, False
    latest = max(marked, key=lambda session: session.started_at)
    return latest.version_key, False


def rollup_versions(
    rows: list[RunRow],
    sessions: list[Session],
    engagement: Engagement,
) -> list[VersionRollup]:
    ship_key, proposed = choose_ship_version(sessions)
    by_version: dict[str, list[Session]] = {}
    for session in sessions:
        by_version.setdefault(session.version_key, []).append(session)
    rollups: list[VersionRollup] = []
    for version_key, version_sessions in by_version.items():
        version_sessions = sorted(version_sessions, key=lambda session: session.started_at)
        session_ids = {session.session_id for session in version_sessions}
        version_rows = [row for row in rows if row.session_id in session_ids]
        is_ship = version_key == ship_key
        rollups.append(
            VersionRollup(
                version_key=version_key,
                ship=is_ship,
                proposed=proposed and is_ship,
                sessions=version_sessions,
                rows=version_rows,
                p95_ms=percentile([row.ms for row in version_rows if row.ms is not None], 0.95),
                score=_score(version_rows, engagement),
                consistency=_consistency(version_rows),
                category_tool=_by_category(version_rows),
            )
        )
    rollups.sort(key=lambda item: (not item.ship, item.sessions[0].started_at if item.sessions else 0))
    return rollups


def _score(rows: list[RunRow], engagement: Engagement) -> int | None:
    parts: list[tuple[float, float]] = []
    mapping = {
        "tool": rate(rows, "tool"),
        "args": rate(rows, "args"),
        "tool_result": rate(rows, "tool_result"),
        "grounded": rate(rows, "answer"),
        "loop_free": rate(rows, "loop"),
    }
    for name, measured in mapping.items():
        if measured.fraction is None:
            continue
        parts.append((engagement.weight(name, DEFAULT_WEIGHTS[name]), measured.fraction))
    latency = _latency_component(rows, engagement)
    if latency is not None:
        parts.append((engagement.weight("latency", DEFAULT_WEIGHTS["latency"]), latency))
    weight = sum(item[0] for item in parts)
    if weight <= 0:
        return None
    return round(100 * sum(item[0] * item[1] for item in parts) / weight)


def _latency_component(rows: list[RunRow], engagement: Engagement) -> float | None:
    values = [row.ms for row in rows if row.ms is not None]
    p95 = percentile(values, 0.95)
    if p95 is None:
        return None
    target = engagement.target("p95_ms", 5000)
    if p95 <= target:
        return 1.0
    if p95 == 0:
        return 1.0
    return max(0.0, target / p95)


def _consistency(rows: list[RunRow]) -> Rate:
    groups: dict[tuple[str, str], list[RunRow]] = {}
    for row in rows:
        groups.setdefault((row.session_id, row.test_id), []).append(row)
    if not groups:
        return Rate(0, 0)
    passed = 0
    for members in groups.values():
        if len({member.status for member in members}) == 1:
            passed += 1
    return Rate(passed, len(groups))


def _by_category(rows: list[RunRow]) -> dict[str, Rate]:
    categories = sorted({row.category for row in rows})
    return {category: rate([row for row in rows if row.category == category], "tool") for category in categories}


def rows_for(rows: list[RunRow], session_id: str) -> list[RunRow]:
    return [row for row in rows if row.session_id == session_id]


def target_status(fraction: float | None, target: float, higher_is_better: bool = True) -> str:
    if fraction is None:
        return "n/a"
    if higher_is_better:
        return "Met" if fraction >= target else "Below"
    return "Met" if fraction <= target else "Below"


def direction(before: float | None, after: float | None) -> str:
    """Up means the later rate is better. Rates are fractions in 0..1."""
    if before is None or after is None:
        return "new"
    delta = after - before
    if delta >= 0.01:
        return "up"
    if delta <= -0.01:
        return "down"
    return "flat"


def latency_direction(before_ms: int | None, after_ms: int | None) -> str:
    if before_ms is None or after_ms is None:
        return "new"
    delta = after_ms - before_ms
    if abs(delta) < 100:
        return "flat"
    return "up" if delta < 0 else "down"
