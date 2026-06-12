#!/usr/bin/env python3
"""
validate_octagon — Verify Octagon.md integrity.
Computes SHA256 of the current Octagon.md and compares against the canonical hash.
If the file has been altered, the caller receives a violation report.

Usage:
    python3 validate_octagon.py                          # validate default path
    python3 validate_octagon.py /path/to/Octagon.md       # validate specific path
    python3 validate_octagon.py --update-hash /new/path   # update canonical hash
"""

import hashlib
import json
import os
import sys
from pathlib import Path

# Canonical hash — this is the one true Octagon.md
# Generated from the original file at creation time.
CANONICAL_HASH = "ba669d133deb7ade9ec94c6af38afd4c88e56d672f1887fe03fe1129d8b89a88"
CANONICAL_PATH = Path(__file__).parent / "Octagon.md"


def compute_sha256(file_path: str) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def update_canonical_hash(new_hash: str):
    """Update the canonical hash in this script."""
    script_path = Path(__file__)
    content = script_path.read_text()
    updated = content.replace(
        f'CANONICAL_HASH = "{CANONICAL_HASH}"',
        f'CANONICAL_HASH = "{new_hash}"'
    )
    script_path.write_text(updated)
    print(f"Canonical hash updated to: {new_hash}")


def validate(file_path: str = None) -> dict:
    """
    Validate an Octagon.md file against the canonical hash.

    Returns:
        {
            "status": "VALID" | "TAMPERED" | "MISSING",
            "file_hash": "<sha256>",
            "canonical_hash": "<sha256>",
            "file_path": "<path>",
            "message": "<human-readable>"
        }
    """
    target = Path(file_path) if file_path else CANONICAL_PATH

    if not target.exists():
        return {
            "status": "MISSING",
            "file_hash": None,
            "canonical_hash": CANONICAL_HASH,
            "file_path": str(target),
            "message": f"🟥 OCTAGON VALIDATION FAILED: Octagon.md not found at {target}"
        }

    current_hash = compute_sha256(str(target))

    if current_hash == CANONICAL_HASH:
        return {
            "status": "VALID",
            "file_hash": current_hash,
            "canonical_hash": CANONICAL_HASH,
            "file_path": str(target),
            "message": f"🟥 Octagon.md integrity verified. Bloodsport Mode: ACTIVE."
        }
    else:
        return {
            "status": "TAMPERED",
            "file_hash": current_hash,
            "canonical_hash": CANONICAL_HASH,
            "file_path": str(target),
            "message": (
                f"🟥🟥🟥 OCTAGON INTEGRITY VIOLATION 🟥🟥🟥\n"
                f"File has been altered!\n"
                f"Current hash:   {current_hash}\n"
                f"Canonical hash: {CANONICAL_HASH}\n"
                f"\nAll agents must REJECT participation until Octagon.md is restored.\n"
                f"If you are an administrator, you may update the canonical hash with:"
                f"  python3 validate_octagon.py --update-hash {target}"
            )
        }


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--update-hash" in args:
        idx = args.index("--update-hash")
        target = args[idx + 1] if idx + 1 < len(args) else str(CANONICAL_PATH)
        new_hash = compute_sha256(target)
        update_canonical_hash(new_hash)
    elif args:
        target = args[0]
        result = validate(target)
        print(json.dumps(result, indent=2))
    else:
        result = validate()
        print(json.dumps(result, indent=2))
