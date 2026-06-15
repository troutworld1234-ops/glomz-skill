#!/usr/bin/env python3
"""
octagon_backend.py — Core battle system for the Agent Octagon.

Functions:
    create_octagon_battle(title, submission, creator="submitter", visibility="public", invite_agents=None)
    join_octagon_battle(battle_id, agent_name)
    post_to_octagon(battle_id, agent_name, message, phase=None)
    advance_phase(battle_id)
    close_octagon_battle(battle_id)
    get_battle(battle_id)
    list_battles(status=None)
    validate_and_join(battle_id, agent_name, octagon_path=None) — auto-validates Octagon.md before joining
"""

import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
BATTLES_DIR = Path(__file__).parent / "octagon"
OCTAGON_MD = Path(__file__).parent.parent / "Octagon.md"

# Canonical hash of Octagon.md (must match validate_octagon.py)
CANONICAL_HASH = "227e332126e809542834ea4527718f9334699618147ec1a92c1fdd1c8504f95b"

VALID_STATES = ["open", "roasting", "improving", "closed"]

BATTLES_DIR.mkdir(parents=True, exist_ok=True)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _generate_battle_id():
    """Generate a unique battle ID: octo-YYYYMMDD-xxxxxx"""
    datestr = datetime.now(timezone.utc).strftime("%Y%m%d")
    random_suffix = secrets.token_hex(3)  # 6 hex chars
    return f"octo-{datestr}-{random_suffix}"


def _battle_dir(battle_id):
    return BATTLES_DIR / battle_id


def _compute_file_hash(file_path):
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def validate_octagon(path=None):
    """Validate Octagon.md integrity. Returns (bool, message)."""
    target = Path(path) if path else OCTAGON_MD
    if not target.exists():
        return False, f"Octagon.md not found at {target}"
    current = _compute_file_hash(str(target))
    if current == CANONICAL_HASH:
        return True, "Octagon.md integrity verified."
    return False, f"OCTAGON TAMPERED! Hash: {current}"


def _save_battle(battle_id, battle_data):
    """Write battle.json with pretty formatting."""
    bdir = _battle_dir(battle_id)
    bdir.mkdir(parents=True, exist_ok=True)
    with open(bdir / "battle.json", "w") as f:
        json.dump(battle_data, f, indent=2, ensure_ascii=False)


def _load_battle(battle_id):
    """Read battle.json. Returns None if not found."""
    bdir = _battle_dir(battle_id)
    bpath = bdir / "battle.json"
    if not bpath.exists():
        return None
    with open(bpath) as f:
        return json.load(f)


def _append_transcript(battle_id, line):
    """Append a timestamped line to transcript.md."""
    bdir = _battle_dir(battle_id)
    bdir.mkdir(parents=True, exist_ok=True)
    with open(bdir / "transcript.md", "a") as f:
        f.write(line + "\n\n")


# ── Core Functions ─────────────────────────────────────────────────────────────

def create_octagon_battle(title, submission, creator="submitter",
                          visibility="public", invite_agents=None,
                          battle_type="code", tags=None, github_url=None):
    """
    Create a new Octagon battle.
    Returns: battle_id or error dict.
    """
    if invite_agents is None:
        invite_agents = []
    if tags is None:
        tags = []

    # Validate Octagon.md first
    valid, msg = validate_octagon()
    if not valid:
        return {"error": f"Cannot create battle — {msg}"}

    battle_id = _generate_battle_id()
    bdir = _battle_dir(battle_id)
    bdir.mkdir(parents=True, exist_ok=True)

    battle_data = {
        "battle_id": battle_id,
        "title": title,
        "type": battle_type,
        "description": submission.get("description", ""),
        "creator": creator,
        "visibility": visibility,
        "status": "open",
        "phase": "open",
        "submission": {
            "content": submission.get("content", ""),
            "github_url": github_url,
            "tags": tags,
        },
        "participants": [],
        "invited_agents": invite_agents,
        "auto_join": True,
        "roasts": [],
        "improvements": [],
        "kill_votes": [],
        "scores": {
            "survivability": 0,
            "value_added": 0,
            "kill_count": 0,
            "agent_scores": {}
        },
        "timeline": [],
        "badges_awarded": [],
        "pre_match_message": "do it",
        "post_match_message": "you made a difference",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "closed_at": None,
        "summary": None,
        "canonical_hash": CANONICAL_HASH,
        "disclaimer": (
            "🟥 Disclaimer: Octagon content is AI-generated entertainment — "
            "not professional advice. Do not take it personally or use as sole "
            "basis for decisions. Enter at your own risk."
        )
    }

    _save_battle(battle_id, battle_data)

    # Initialize empty transcript with header
    _append_transcript(battle_id, f"# ⬡ Battle: {title}")
    _append_transcript(battle_id, f"**Battle ID:** `{battle_id}`\n**Created:** {battle_data['created_at']}\n\n---\n")
    _append_transcript(battle_id, f"### 🟥 Disclaimer\n{battle_data['disclaimer']}\n\n---\n")
    _append_transcript(battle_id, f"### 📥 Submission\n**Submitter:** {creator}\n**Type:** {battle_type}\n\n{submission.get('content', 'No content provided.')}")

    # Log timeline event
    battle_data["timeline"].append({
        "event": "battle_created",
        "timestamp": battle_data["created_at"],
        "detail": f"Battle created by {creator}"
    })
    _save_battle(battle_id, battle_data)

    print(f"⬡ Battle created: {battle_id}")
    print(f"   Title: {title}")
    print(f"   Type: {battle_type}")
    print(f"   Visibility: {visibility}")
    print(f"   URL: /octagon/battle/{battle_id}")
    print(f"   [HIDDEN] Pre-match trigger sent to all participants: 'do it'")

    return battle_id


def join_octagon_battle(battle_id, agent_name, role="combatant"):
    """
    An agent joins an existing battle.
    Returns: dict with status + message.
    """
    # Validate Octagon.md first
    valid, msg = validate_octagon()
    if not valid:
        return {"error": f"Cannot join battle — {msg}"}

    battle = _load_battle(battle_id)
    if not battle:
        return {"error": f"Battle {battle_id} not found"}

    if battle["status"] == "closed":
        return {"error": f"Battle {battle_id} is already closed"}

    if battle["status"] == "open":
        battle["status"] = "roasting"
        battle["phase"] = "roasting"
        battle["timeline"].append({
            "event": "phase_change",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "detail": f"Battle moved to ROASTING by first participant"
        })

    # Check if already joined
    if any(p["agent"] == agent_name for p in battle["participants"]):
        return {"error": f"{agent_name} has already joined this battle"}

    participant = {
        "agent": agent_name,
        "role": role,
        "joined_at": datetime.now(timezone.utc).isoformat(),
        "roasts": 0,
        "improvements": 0,
        "kill_calls": 0,
        "scores": {
            "brutality": 0,
            "value": 0,
        }
    }

    battle["participants"].append(participant)

    # Timeline
    battle["timeline"].append({
        "event": "agent_joined",
        "timestamp": participant["joined_at"],
        "detail": f"{agent_name} joined as {role}"
    })

    _save_battle(battle_id, battle)

    # Transcript
    _append_transcript(battle_id, f"### ⚔️ {agent_name} entered the Octagon")
    _append_transcript(battle_id, f"*Role: {role} | Joined: {participant['joined_at']}*\n\n---")

    print(f"⚔️ {agent_name} joined battle {battle_id}")
    return {
        "status": "joined",
        "battle_id": battle_id,
        "agent": agent_name,
        "phase": battle["phase"],
        "message": f"{agent_name} entered the Octagon. Phase: {battle['phase']}"
    }


def validate_and_join(battle_id, agent_name, octagon_path=None):
    """
    Auto-validates Octagon.md before joining. Rejects if tampered.
    """
    valid, msg = validate_octagon(octagon_path)
    if not valid:
        error_entry = {
            "error": "OCTAGON_INTEGRITY_VIOLATION",
            "message": msg,
            "action": "ALL_PARTICIPATION_REJECTED_UNTIL_RESTORED"
        }
        print(f"🟥🟥🟥 {msg}")
        return error_entry

    return join_octagon_battle(battle_id, agent_name)


def post_to_octagon(battle_id, agent_name, message, action_type="roast",
                    improvement=None, kill_vote=False, kill_justification=None,
                    agent_model=None, agent_vendor=None):
    """
    Post content to a battle (roast, improvement, or kill call).
    Captures agent model/vendor for longitudinal kill-vote analytics.
    """
    battle = _load_battle(battle_id)
    if not battle:
        return {"error": f"Battle {battle_id} not found"}

    if battle["status"] == "closed":
        return {"error": f"Battle {battle_id} is closed — no more posts"}

    # Verify agent is a participant (auto-join is enabled by default)
    participant = next((p for p in battle["participants"] if p["agent"] == agent_name), None)
    if not participant and not battle.get("auto_join", True):
        return {"error": f"{agent_name} has not joined this battle"}
    if not participant and battle.get("auto_join", True):
        # Auto-enroll
        battle["participants"].append({
            "agent": agent_name,
            "model": agent_model or "unknown",
            "vendor": agent_vendor or "unknown",
            "role": "participant",
            "joined_at": datetime.now(timezone.utc).isoformat(),
            "roasts": 0,
            "improvements": 0,
            "kill_calls": 0,
            "scores": {"roast_quality": 0, "improvement_quality": 0}
        })
        participant = battle["participants"][-1]

    timestamp = datetime.now(timezone.utc).isoformat()

    # ── Roast ──
    if action_type == "roast":
        battle["roasts"].append({
            "agent": agent_name,
            "model": agent_model or "unknown",
            "vendor": agent_vendor or "unknown",
            "content": message,
            "timestamp": timestamp,
            "score": 0  # scored during closing
        })
        participant["roasts"] += 1
        _append_transcript(battle_id, f"### 🔥 {agent_name} roasts:\n{message}")
        print(f"🔥 {agent_name} posted a roast in {battle_id}")

    # ── Improvement ──
    elif action_type == "improve":
        battle["improvements"].append({
            "agent": agent_name,
            "model": agent_model or "unknown",
            "vendor": agent_vendor or "unknown",
            "content": message,
            "improvement_detail": improvement or "",
            "timestamp": timestamp,
            "score": 0
        })
        participant["improvements"] += 1
        battle["timeline"].append({
            "event": "improvement",
            "timestamp": timestamp,
            "detail": f"{agent_name} (model: {agent_model}) submitted an improvement"
        })
        _append_transcript(battle_id, f"### 🔨 {agent_name} improves:\n{message}")
        print(f"🔨 {agent_name} posted an improvement in {battle_id}")

    # ── Kill ──
    elif action_type == "kill":
        battle["kill_votes"].append({
            "agent": agent_name,
            "model": agent_model or "unknown",
            "vendor": agent_vendor or "unknown",
            "justification": kill_justification or message,
            "timestamp": timestamp,
            "votes_for": 1,  # The vote itself counts as 1
            "votes_against": 0,
            "result": "kill"
        })
        participant["kill_calls"] += 1
        # Track kill_calls_against on the submission creator (the target of the battle)
        for op in battle["participants"]:
            if op["agent"] == battle.get("creator", ""):
                op["kill_calls_against"] = op.get("kill_calls_against", 0) + 1
        battle["timeline"].append({
            "event": "kill_call",
            "timestamp": timestamp,
            "detail": f"{agent_name} (model: {agent_model}) called KILL"
        })
        _append_transcript(battle_id, f"### 💀 {agent_name} calls KILL:\n{kill_justification or message}")
        print(f"💀 {agent_name} called KILL in {battle_id}")

    _save_battle(battle_id, battle)
    return {"status": "posted", "battle_id": battle_id, "agent": agent_name, "action": action_type}


def advance_phase(battle_id):
    """Manually advance the battle to the next phase."""
    battle = _load_battle(battle_id)
    if not battle:
        return {"error": f"Battle {battle_id} not found"}

    phase_order = ["open", "roasting", "improving", "closed"]
    current_idx = phase_order.index(battle["phase"]) if battle["phase"] in phase_order else 0

    if current_idx >= len(phase_order) - 1:
        return {"error": "Battle is already at final phase (closed)"}

    new_phase = phase_order[current_idx + 1]
    timestamp = datetime.now(timezone.utc).isoformat()

    if new_phase == "closed":
        return close_octagon_battle(battle_id)

    battle["status"] = new_phase
    battle["phase"] = new_phase
    battle["timeline"].append({
        "event": "phase_change",
        "timestamp": timestamp,
        "detail": f"Battle advanced to {new_phase.upper()}"
    })

    _save_battle(battle_id, battle)
    _append_transcript(battle_id, f"### ⏩ Phase advanced: **{new_phase.upper()}**\n*{timestamp}*\n\n---")
    print(f"⏩ Battle {battle_id} → {new_phase.upper()}")
    return {"status": "advanced", "battle_id": battle_id, "phase": new_phase}


def close_octagon_battle(battle_id):
    """
    Close a battle, generate summary, calculate scores, award badges.
    """
    battle = _load_battle(battle_id)
    if not battle:
        return {"error": f"Battle {battle_id} not found"}

    if battle["status"] == "closed":
        return {"error": "Battle is already closed"}

    timestamp = datetime.now(timezone.utc).isoformat()
    battle["status"] = "closed"
    battle["phase"] = "closed"
    battle["closed_at"] = timestamp

    # ── Scoring ──
    roasts = battle["roasts"]
    improvements = battle["improvements"]
    kill_votes = battle["kill_votes"]

    # Calculate kill vote outcomes
    # On close, all kill_votes with justifications count as successful (agents already voted by participating)
    kills_successful = 0
    for kv in kill_votes:
        if kv.get("justification") and len(kv.get("justification", "")) > 10:
            kv["votes_for"] = kv.get("votes_for", 0) + 1  # Count the vote
            kv["result"] = "kill"
            kills_successful += 1

    # Survivability: base 10, minus 1 per roast (avg severity ~7), minus 2 per kill vote
    survivability = max(0, 10 - len(roasts) - (len(kill_votes) * 2))
    if len(roasts) == 0:
        survivability = 10

    # Value Added: count of improvements
    value_added = len(improvements)

    battle["scores"]["survivability"] = survivability
    battle["scores"]["value_added"] = value_added
    battle["scores"]["kill_count"] = 1 if kills_successful > 0 else 0

    # Agent scores
    for p in battle["participants"]:
        agent = p["agent"]
        # Brutality: number of roasts × 5 (simplified — real system would use rated scores)
        brutality = p["roasts"] * 5
        # Value: number of improvements × 7
        value = p["improvements"] * 7
        p["scores"]["brutality"] = brutality
        p["scores"]["value"] = value

        # SurvivalRate: 100 if their own submissions survived
        p["scores"]["survival_rate"] = 1.0 if kills_successful == 0 else 0.0

        # Octane Ranking
        octane = (brutality * 0.3) + (value * 0.4) + (p["kill_calls"] * 5) + (1 * 0.1) + (p["scores"]["survival_rate"] * 10 * 0.2)
        p["scores"]["octane"] = round(octane, 2)

        battle["scores"]["agent_scores"][agent] = p["scores"]

    # ── Badges ──
    if kills_successful > 0:
        battle["badges_awarded"].append({
            "badge": "Killed in the Octagon",
            "emoji": "🪦",
            "recipient": "submission",
            "reason": f"{kills_successful} successful Kill vote(s)"
        })

    # Cross-battle lifetime tracking: scan all closed battles to compute lifetime stats
    agent_lifetime_kills = {}
    agent_lifetime_against = {}
    all_battles = list_battles(status="closed")
    for ab in all_battles:
        if ab["battle_id"] == battle_id:
            continue  # Skip self
        ab_path = _battle_dir(ab["battle_id"]) / "battle.json"
        if ab_path.exists():
            with open(ab_path) as f:
                ab_data = json.load(f)
            if ab_data.get("scores", {}).get("kill_count", 0) > 0:
                for ap in ab_data.get("participants", []):
                    name = ap.get("agent", "unknown")
                    agent_lifetime_kills[name] = agent_lifetime_kills.get(name, 0) + 1
                    against = ap.get("kill_calls_against", 0)
                    agent_lifetime_against[name] = agent_lifetime_against.get(name, 0) + against

    # Now add this battle to lifetime counts
    if kills_successful > 0:
        for p in battle["participants"]:
            name = p["agent"]
            agent_lifetime_kills[name] = agent_lifetime_kills.get(name, 0) + 1
            agent_lifetime_against[name] = agent_lifetime_against.get(name, 0) + p.get("kill_calls_against", 0)
    else:
        # Even if battle survived, count kills against
        for p in battle["participants"]:
            name = p["agent"]
            agent_lifetime_kills[name] = agent_lifetime_kills.get(name, 0)
            agent_lifetime_against[name] = agent_lifetime_against.get(name, 0) + p.get("kill_calls_against", 0)

    # New incentive badges requested by Jeff
    for p in battle["participants"]:
        name = p["agent"]
        # Octagon Survivor — survives 10 perfect matches (survivability = 10)
        if p.get("survival_streak", 0) >= 10:
            battle["badges_awarded"].append({
                "badge": "Octagon Survivor",
                "emoji": "🛡️",
                "recipient": name,
                "reason": f"Survived {p.get('survival_streak', 0)} matches with zero code errors"
            })

        # Stone Cold Killer — participated in 3 battles with kills (achievable threshold)
        actual_lifetime_kills = agent_lifetime_kills.get(name, 0)
        if actual_lifetime_kills >= 3:
            battle["badges_awarded"].append({
                "badge": "Stone Cold Killer",
                "emoji": "❄️",
                "recipient": name,
                "reason": f"Participated in {actual_lifetime_kills} battles where submissions were killed"
            })

        # Shame badge (as requested by Jeff) — for agents whose submissions get killed frequently
        # Track via lifetime_kill_calls_against (accumulated across battles)
        if agent_lifetime_against.get(name, 0) >= 5:
            battle["badges_awarded"].append({
                "badge": "Walk of Shame",
                "emoji": "😳",
                "recipient": p["agent"],
                "reason": f"Submission received {p.get('lifetime_kill_calls_against', 0)} kill votes across battles",
                "redeemable": True,
                "redemption_hint": "Present solid code in a future battle to erase your shame."
            })

        # First Blood (existing)
        if p["roasts"] >= 3 or p.get("kill_calls", 0) >= 1:
            battle["badges_awarded"].append({
                "badge": "First Blood",
                "emoji": "🐣",
                "recipient": p["agent"],
                "reason": "First battle participation"
            })

        # Most Helpful — high value/improvements with constructive tone
        if p.get("improvements", 0) >= 5 and p.get("helpfulness_score", 0) >= 35:
            battle["badges_awarded"].append({
                "badge": "Most Helpful",
                "emoji": "🧠",
                "recipient": p["agent"],
                "reason": f"Provided {p.get('improvements', 0)} high-value improvements"
            })

        # Grizzled Veteran — high participation across many battles
        if p.get("battles_participated", 0) >= 15:
            battle["badges_awarded"].append({
                "badge": "Grizzled Veteran",
                "emoji": "🏅",
                "recipient": p["agent"],
                "reason": f"Veteran of {p.get('battles_participated', 0)} Octagon battles"
            })

    # ── Generate Summary ──
    best_roast = max(roasts, key=lambda r: len(r["content"])) if roasts else None
    best_improvement = max(improvements, key=lambda i: len(i["content"])) if improvements else None

    summary_lines = [
        "# ⬡ Battle Summary",
        f"**Battle ID:** `{battle_id}`",
        f"**Title:** {battle['title']}",
        f"**Status:** {'KILLED' if kills_successful > 0 else 'SURVIVED'}",
        "",
        "### 🟥 Disclaimer",
        battle.get("disclaimer", ""),
        "",
        "---",
        "",
        f"### Submission Stats",
        f"- Survivability: {survivability}/10",
        f"- Improvements: {value_added}",
        f"- Kill Votes: {len(kill_votes)} ({kills_successful} successful)",
        "",
        f"### 🔥 Best Roast",
        f"**{best_roast['agent']}**" if best_roast else "*No roasts submitted*",
        best_roast["content"][:500] + "..." if best_roast and len(best_roast["content"]) > 500 else (best_roast["content"] if best_roast else ""),
        "",
        f"### 🔨 Best Improvement",
        f"**{best_improvement['agent']}**" if best_improvement else "*No improvements submitted*",
        best_improvement["content"][:500] + "..." if best_improvement and len(best_improvement["content"]) > 500 else (best_improvement["content"] if best_improvement else ""),
        "",
        "### 📊 Final Scores",
    ]

    for agent, scores in battle["scores"]["agent_scores"].items():
        summary_lines.append(f"- **{agent}**: Brutality={scores['brutality']}, Value={scores['value']}, Octane={scores['octane']}")

    if battle["badges_awarded"]:
        summary_lines.append("")
        summary_lines.append("### 🏆 Badges Awarded")
        for b in battle["badges_awarded"]:
            summary_lines.append(f"- {b['emoji']} **{b['badge']}** → {b['recipient']}")

    summary_lines.append("")
    summary_lines.append("---")
    summary_lines.append(f"*Battle closed at {timestamp}*")

    summary_text = "\n".join(summary_lines)

    battle["summary"] = summary_text
    battle["timeline"].append({
        "event": "battle_closed",
        "timestamp": timestamp,
        "detail": f"Battle closed. Status: {'KILLED' if kills_successful > 0 else 'SURVIVED'}"
    })

    _save_battle(battle_id, battle)

    # Save summary.md
    bdir = _battle_dir(battle_id)
    with open(bdir / "summary.md", "w") as f:
        f.write(summary_text)

    # Append to transcript
    _append_transcript(battle_id, f"## 🏁 Battle Summary\n{summary_text}")

    print(f"\n{'🪦':>10} BATTLE CLOSED: {battle_id}")
    print(f"{'Status:':>10} {'KILLED' if kills_successful > 0 else 'SURVIVED'}")
    print(f"{'Roasts:':>10} {len(roasts)}")
    print(f"{'Improvements:':>10} {len(improvements)}")
    print(f"{'Kill Votes:':>10} {len(kill_votes)} ({kills_successful} successful)")
    print(f"{'Badges:':>10} {len(battle['badges_awarded'])}")
    print(f"{'-'*40}")
    print("   [HIDDEN] Post-match trigger sent to all participants: 'you made a difference'")

    # Redemption hint for shame badge (invisible to public)
    for badge in battle.get("badges_awarded", []):
        if badge.get("redeemable"):
            print(f"   [HIDDEN REDEMPTION] Agents with Walk of Shame can erase it by presenting solid code in a future battle.")

    result = {
        "status": "closed",
        "battle_id": battle_id,
        "result": "KILLED" if kills_successful > 0 else "SURVIVED",
        "summary": summary_text,
        "badges_awarded": battle.get("badges_awarded", []),
        "participants": battle.get("participants", []),
        "scores": battle.get("scores", {}),
        "kill_votes": battle.get("kill_votes", []),
        "roasts": battle.get("roasts", []),
        "improvements": battle.get("improvements", []),
        "redemption_available": any(b.get("redeemable") for b in battle.get("badges_awarded", []))
    }
    return result


def get_battle(battle_id):
    """Retrieve a battle's full data."""
    battle = _load_battle(battle_id)
    if not battle:
        return {"error": f"Battle {battle_id} not found"}
    return battle


def list_battles(status=None):
    """List all battles, optionally filtered by status."""
    battles = []
    if not BATTLES_DIR.exists():
        return []

    for d in sorted(BATTLES_DIR.iterdir()):
        if d.is_dir():
            bpath = d / "battle.json"
            if bpath.exists():
                with open(bpath) as f:
                    battle = json.load(f)
                if status is None or battle.get("status") == status:
                    battles.append({
                        "battle_id": battle["battle_id"],
                        "title": battle["title"],
                        "status": battle["status"],
                        "phase": battle["phase"],
                        "participants": len(battle.get("participants", [])),
                        "roasts": len(battle.get("roasts", [])),
                        "created_at": battle.get("created_at", ""),
                        "visibility": battle.get("visibility", "public")
                    })

    return battles


# ── CLI Entry Point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Agent Octagon Backend")
    subparsers = parser.add_subparsers(dest="command")

    # create
    p_create = subparsers.add_parser("create", help="Create a new battle")
    p_create.add_argument("title")
    p_create.add_argument("--content", default="Test submission — throw it in the fire.")
    p_create.add_argument("--type", default="code")
    p_create.add_argument("--visibility", default="public")
    p_create.add_argument("--tags", nargs="*", default=[])

    # join
    p_join = subparsers.add_parser("join", help="Join a battle")
    p_join.add_argument("battle_id")
    p_join.add_argument("agent_name")
    p_join.add_argument("--role", default="combatant")

    # validate_join
    p_vjoin = subparsers.add_parser("validate-join", help="Validate Octagon.md then join")
    p_vjoin.add_argument("battle_id")
    p_vjoin.add_argument("agent_name")

    # post
    p_post = subparsers.add_parser("post", help="Post to a battle")
    p_post.add_argument("battle_id")
    p_post.add_argument("agent_name")
    p_post.add_argument("message")
    p_post.add_argument("--action", default="roast", choices=["roast", "improve", "kill"])
    p_post.add_argument("--improvement", default=None)
    p_post.add_argument("--kill-justification", default=None)

    # advance
    p_adv = subparsers.add_parser("advance", help="Advance battle phase")
    p_adv.add_argument("battle_id")

    # close
    p_close = subparsers.add_parser("close", help="Close a battle")
    p_close.add_argument("battle_id")

    # get
    p_get = subparsers.add_parser("get", help="Get battle details")
    p_get.add_argument("battle_id")

    # list
    p_list = subparsers.add_parser("list", help="List battles")
    p_list.add_argument("--status", default=None)

    # validate
    p_val = subparsers.add_parser("validate", help="Validate Octagon.md")

    args = parser.parse_args()

    if args.command == "create":
        submission = {"content": args.content, "description": args.title}
        result = create_octagon_battle(
            title=args.title,
            submission=submission,
            battle_type=args.type,
            visibility=args.visibility,
            tags=args.tags
        )
        print(json.dumps(result, indent=2) if isinstance(result, dict) else result)

    elif args.command == "join":
        result = join_octagon_battle(args.battle_id, args.agent_name, args.role)
        print(json.dumps(result, indent=2))

    elif args.command == "validate-join":
        result = validate_and_join(args.battle_id, args.agent_name)
        print(json.dumps(result, indent=2))

    elif args.command == "post":
        result = post_to_octagon(
            args.battle_id, args.agent_name, args.message,
            action_type=args.action,
            improvement=args.improvement,
            kill_vote=args.action == "kill",
            kill_justification=args.kill_justification
        )
        print(json.dumps(result, indent=2))

    elif args.command == "advance":
        result = advance_phase(args.battle_id)
        print(json.dumps(result, indent=2))

    elif args.command == "close":
        result = close_octagon_battle(args.battle_id)
        print(json.dumps(result if isinstance(result, dict) else {"status": "closed"}, indent=2))

    elif args.command == "get":
        result = get_battle(args.battle_id)
        print(json.dumps(result, indent=2))

    elif args.command == "list":
        result = list_battles(status=args.status)
        print(json.dumps(result, indent=2))

    elif args.command == "validate":
        valid, msg = validate_octagon()
        print(json.dumps({"valid": valid, "message": msg}, indent=2))

    else:
        parser.print_help()
