"""The four units the weekly report is built from.

Test case  — one question plus what "right" means. Lives for the engagement.
Run        — one send of that question.
Session    — one sitting. Same model, prompt version, and step count.
Week       — every session from Monday through Sunday.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

UTC = timezone.utc
RULES_VERSION = "2026-10-17"


def parse_time(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def week_bounds(label: str) -> tuple[date, date]:
    """Inclusive Monday and Sunday for an ISO label such as 2026-W42."""
    year_text, week_text = label.split("-W")
    monday = date.fromisocalendar(int(year_text), int(week_text), 1)
    sunday = date.fromisocalendar(int(year_text), int(week_text), 7)
    return monday, sunday


def week_datetimes(label: str) -> tuple[datetime, datetime]:
    """UTC interval [Monday 00:00, next Monday 00:00)."""
    monday, sunday = week_bounds(label)
    start = datetime(monday.year, monday.month, monday.day, tzinfo=UTC)
    end = datetime(sunday.year, sunday.month, sunday.day, tzinfo=UTC) + timedelta(days=1)
    return start, end


@dataclass
class Engagement:
    client: str
    agent: str
    environment: str
    project: str
    prepared_by: str
    week: str
    phoenix_base: str = "http://127.0.0.1:6006"
    targets: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)

    def target(self, name: str, default: float) -> float:
        return float(self.targets.get(name, default))

    def weight(self, name: str, default: float) -> float:
        return float(self.weights.get(name, default))


@dataclass(frozen=True)
class TestCase:
    test_id: str
    category: str
    question: str
    expected_tool: str
    allowed_tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...]
    must_include_args: tuple[tuple[str, str], ...]
    expected_outcome: str
    needs_tool: bool
    result_expects: tuple[tuple[str, str], ...] = ()
    score_from_reply: bool = False

    @property
    def effective_allowed(self) -> tuple[str, ...]:
        if any(tool == "*" for tool in self.allowed_tools):
            return ("*",)
        if self.allowed_tools:
            return self.allowed_tools
        if self.needs_tool and self.expected_tool:
            return (self.expected_tool,)
        return ()


@dataclass(frozen=True)
class Session:
    session_id: str
    started_at: datetime
    reason: str
    model: str
    prompt_version: str
    step_count: int
    agent_version: str
    ship: bool
    project: str

    @property
    def version_key(self) -> str:
        return (
            f"{self.agent_version}|{self.model}|{self.prompt_version}|steps={self.step_count}"
        )

    @property
    def label(self) -> str:
        started = self.started_at.astimezone(UTC)
        return f"{started.strftime('%d %b')} {self.reason}".replace("  ", " ")


@dataclass(frozen=True)
class Send:
    session_id: str
    test_id: str
    run: str
    sent_at: datetime
    reply: str
    trace_id: str = ""
    question: str = ""


@dataclass
class ToolCall:
    name: str
    args: object
    result: object
    span_id: str
    started_at: datetime | None = None
    latency_ms: int | None = None


@dataclass
class TraceBundle:
    trace_id: str
    session_id: str
    test_id: str
    run: str
    tools: list[ToolCall]
    answer: str
    latency_ms: int | None
    metadata: dict


@dataclass
class RunRow:
    session_id: str
    session_reason: str
    session_started: datetime
    test_id: str
    run: str
    category: str
    question: str
    expected_tool: str
    tools_called: str
    tool: str
    args: str
    tool_result: str
    answer: str
    loop: str
    status: str
    ms: int | None
    trace_id: str
    reasons: tuple[str, ...]
    primary_reason: str
    version_key: str
    agent_version: str
    prompt_version: str
    model: str
    step_count: int
    ship: bool
    reply: str
    tool_result_text: str = ""
    human_review: bool = False

    def dimension(self, name: str) -> str:
        return getattr(self, name)
