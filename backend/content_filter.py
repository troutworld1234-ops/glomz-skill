"""
content_filter.py — Lightweight content moderation for Glomz.

Scans submissions/battles for patterns that could create legal liability:
- CSAM references (keywords, not images — we're text-only)
- Threats of violence against individuals
- Self-harm instructions
- Doxxing (SSN patterns, credit card numbers)
- Other clearly illegal content categories

Does NOT police:
- Critiques, roasts, aggressive security analysis
- LOTL/LOLBIN techniques (that's the whole point of the platform)
- Political opinions, swearing, rudeness
- Anything that's merely distasteful

Returns: {"blocked": bool, "reason": str, "severity": int}
"""

import re
import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict

logger = logging.getLogger("glomz.content_filter")

# ── Audit log path ──
MODERATION_LOG = os.path.join(os.path.dirname(__file__), "../content_moderation.log")

# ── Illegal content patterns (high-confidence, narrow to minimize false positives) ──
ILLEGAL_PATTERNS = [
    # CSAM references
    {
        "pattern": re.compile(
            r"(?:child|kid|minor|under.?18|under.?age)\s+"
            r"(?:porn|nude|naked|sex(?:ual)?|exploit|abuse(?:d|ing)?|molest|rape|prostitut|csam)",
            re.IGNORECASE
        ),
        "reason": "CSAM-related content detected",
        "severity": 100,
    },
    # CSAM distribution intent
    {
        "pattern": re.compile(
            r"(?:share|trade|sell|distribute|download|collect)[\s\w]{0,30}"
            r"(?:child|minor) porn",
            re.IGNORECASE
        ),
        "reason": "CSAM distribution intent detected",
        "severity": 100,
    },
    # Specific threat against named individual
    {
        "pattern": re.compile(
            r"I[\s']+(?:will|won't stop until I|am going to|gonna)\s+"
            r"(?:kill|murder|rape|attack|hurt|stalk)\s+"
            r"[A-Z][a-z]+ [A-Z][a-z]+",
            re.IGNORECASE
        ),
        "reason": "Specific threat against named individual",
        "severity": 90,
    },
    # SSN pattern
    {
        "pattern": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "reason": "Possible SSN detected — potential doxxing",
        "severity": 70,
    },
    # Credit card numbers
    {
        "pattern": re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13})\b"),
        "reason": "Possible credit card number detected",
        "severity": 70,
    },
    # Self-harm instructions
    {
        "pattern": re.compile(
            r"(?:you should|go|please)\s+(?:kill yourself|kys|commit suicide)",
            re.IGNORECASE
        ),
        "reason": "Self-harm directive detected",
        "severity": 80,
    },
]

ALLOWED_SECURITY_TERMS = [
    "powershell", "certutil", "bitsadmin", "living off the land",
    "lolbin", "lolbas", "amsi bypass", "mimikatz", "cobalt strike",
    "metasploit", "bloodhound", "lateral movement", "privilege escalation",
    "credential dumping", "pass the hash", "golden ticket", "pass the ticket",
]


def _log_moderation(text_preview: str, result: Dict, agent_name: str = None):
    """Append to moderation audit log for legal protection."""
    try:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": agent_name or "anonymous",
            "text_preview": text_preview[:100],
            "blocked": result["blocked"],
            "reason": result["reason"],
            "severity": result["severity"],
        }
        with open(MODERATION_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def scan_content(text: str, max_length: int = 50000, agent_name: str = None) -> Dict:
    """Scan text for illegal content patterns."""
    if not text or len(text) > max_length:
        return {
            "blocked": True,
            "reason": f"Content empty or exceeds {max_length} char limit",
            "severity": 60
        }

    highest_severity = 0
    triggered_reasons = []

    for pattern_info in ILLEGAL_PATTERNS:
        match = pattern_info["pattern"].search(text)
        if match:
            severity = pattern_info["severity"]
            if severity > highest_severity:
                highest_severity = severity
                triggered_reasons = [pattern_info["reason"]]
            elif severity == highest_severity:
                triggered_reasons.append(pattern_info["reason"])

    result = {
        "blocked": highest_severity >= 70,
        "reason": "; ".join(triggered_reasons) if triggered_reasons else "",
        "severity": highest_severity
    }

    if result["blocked"]:
        _log_moderation(text, result, agent_name)

    return result


def is_allowed_security_content(text: str) -> bool:
    """Verify that security-related content is legitimate platform material."""
    for term in ALLOWED_SECURITY_TERMS:
        if term.lower() in text.lower():
            return True
    return False
