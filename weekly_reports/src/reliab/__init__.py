"""Weekly agent-reliability reports from Phoenix traces and a send log."""

from reliab.models import RULES_VERSION
from reliab.pipeline import run_week

__all__ = ["run_week", "RULES_VERSION"]
__version__ = "0.1.0"
