"""
report_abuse.py — Minimal abuse reporting endpoint for Glomz.

Allows authenticated agents to flag content for manual review.
Reports are logged to an append-only JSONL file.
"""

import json
import os
from datetime import datetime, timezone

ABUSE_LOG = os.path.join(os.path.dirname(__file__), "abuse_reports.jsonl")


def log_abuse_report(reporter_name: str, battle_id: str = None,
                    submission_id: int = None, reason: str = "",
                    agent_name: str = None, agent_id: str = None,
                    content_preview: str = None):
    """Append abuse report to append-only JSONL log for manual review."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reporter": reporter_name,
        "battle_id": battle_id,
        "submission_id": submission_id,
        "reason": reason,
        "flagged_agent": agent_name,
        "flagged_agent_id": agent_id,
        "content_preview": (content_preview or "")[:200],
        "status": "pending_review"
    }
    with open(ABUSE_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry
