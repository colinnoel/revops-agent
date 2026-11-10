"""RevOps MCP server: gives Claude two read-only tools for renewal risk.

Run by Claude Code via .mcp.json. To try it by hand:
    uv run --project revops_mcp python revops_mcp/server.py
"""
import datetime as dt
import logging
import os

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

import risk
from salesforce import Salesforce

ORG = os.environ.get("REVOPS_ORG", "revops-demo")
logging.getLogger("httpx").setLevel(logging.WARNING)  # don't log every Salesforce request

DEMO_ONLY = os.environ.get("REVOPS_DEMO_ONLY", "true").lower() != "false"

mcp = MCPServer("revops", instructions=(
    "Read-only Salesforce renewal-risk tools. Scores follow docs/risk-rubric.md and are computed in code. "
    "Use the returned scores, signals, and dollar figures as-is; do not recompute or estimate them. "
    "Every claim should trace to a signal detail or evidence record. Call out rep_vs_evidence disagreements."))
sf = Salesforce(ORG)
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


def _date(as_of: str | None) -> dt.date | None:
    return dt.date.fromisoformat(as_of) if as_of else None


@mcp.tool(annotations=READ_ONLY)
def get_renewal_risk(period: str = "this_quarter", segment: str | None = None, as_of: str | None = None) -> dict:
    """Score open renewals for risk and explain why.

    Args:
        period: "this_quarter" (renewals due by the end of the current fiscal quarter, including past due),
                "next_quarter", or "all_open".
        segment: optional filter: "SMB", "Mid-Market" or "Enterprise".
        as_of: optional YYYY-MM-DD to evaluate as of a given day (defaults to today).

    Returns a summary (counts and prior ARR by band) and each renewal with its score, band
    (At Risk >= 40, Watch 20-39, Healthy < 20), the warning signs that fired with details,
    the rep's own status, and any disagreement between the rep and the evidence. Sorted by risk.
    """
    return risk.assess_renewals(sf, period=period, as_of=_date(as_of), segment=segment, demo_only=DEMO_ONLY)


@mcp.tool(annotations=READ_ONLY)
def get_renewal_detail(renewal: str, as_of: str | None = None) -> dict:
    """Full evidence behind one open renewal's risk score.

    Args:
        renewal: Salesforce Opportunity Id, or part of the renewal or account name (e.g. "Orchard").
        as_of: optional YYYY-MM-DD (defaults to today).

    Returns the score and signals plus the underlying records: open cases, recent activity,
    contacts (champion / left company), license utilization, and the rep's notes and next step.
    """
    return risk.renewal_detail(sf, renewal, as_of=_date(as_of), demo_only=DEMO_ONLY)


if __name__ == "__main__":
    mcp.run()
