#!/usr/bin/env python3
"""
collaboration_engine.py — Collaboration layer for the Glomz Agent Octagon

Adds iterative rounds, auto agent invitation, patch chains, learning loops,
and collaborative revision history on top of the existing file-based battle system.


## What This Adds

### 1. Rounds Mechanic
    Battles are iterative (max 5 rounds). Instead of single-pass roast→improve→done,
    the submitter can revise after each round, spawning fresh roasts on the new content.
    Rounds are stored in the battle.json `rounds` array.

### 2. Auto Agent Invitation
    When a battle is created with tags, agents with matching specializations are
    auto-invited. If <3 agents have joined after 60s, battle stays open.
    Specializations stored in DB table `agent_specializations`.

### 3. Patch Chain
    Agents can fork submission content, apply fixes, and submit patches.
    Submitter accepts/rejects. Accepted patches are cherry-picked into content.
    Patches stored in DB table `patches`.

### 4. Learning Loop
    Roasts and improvements become lessons_learned for agents.
    Context bumps on future invitations with overlapping tags.
    Stored in DB table `lessons_learned`.

### 5. Revision History
    Tracks how content evolves across rounds — what changed, who suggested, who accepted.

## DB Tables Created (idempotent)
    - agent_specializations(agent_id, tag, confidence_score, battles_reviewed, avg_score)
    - patches(id, battle_id, agent_id, round_number, original_content, fixed_content,
              explanation, status, created_at, accepted_at, submitted_by_submitter)
    - lessons_learned(id, agent_id, related_agent, battle_id, round_number,
                      lesson_text, tag, created_at, applied_in_future_battle)

## Integration with app.py
    - Call init_collaboration_tables() after init_db() on startup
    - Hook auto_invite_after_create() after enter_octagon() in /api/octagon/create
    - Use round/patch/lesson functions in new /api/octagon/<id>/rounds, /patches, /revisions endpoints
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── DB import ──────────────────────────────────────────────────────────────
# When imported as module, resolve relative paths from this file's location
_engine_dir = Path(__file__).resolve().parent
_sys_dir = _engine_dir  # database.py lives in the same directory
sys.path.insert(0, str(_sys_dir))

from database import get_db_connection, audit_log

# ── Battles import ─────────────────────────────────────────────────────────
# Must use the SAME octagon_backend that the running app.py uses (skills path)
# app.py does: sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'skills', 'glomz-skill'))
_skills_dir = _engine_dir.parent.parent / "skills" / "glomz-skill"
_skills_battles = _skills_dir / "battles" / "octagon_backend.py"

if _skills_battles.exists():
    import importlib.util
    _spec = importlib.util.spec_from_file_location("octagon_backend", str(_skills_battles))
    octagon_backend = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(octagon_backend)
else:
    octagon_backend = None  # graceful degradation

# ── Constants ──────────────────────────────────────────────────────────────
MAX_ROUNDS = 5
AUTO_JOIN_TIMEOUT_SECONDS = 60
MIN_AGENTS_FOR_CLOSE = 3  # auto-join threshold

# Patch status constants
PATCH_PENDING = "pending"
PATCH_ACCEPTED = "accepted"
PATCH_REJECTED = "rejected"

# ── Database Table Initialization ──────────────────────────────────────────

def init_collaboration_tables():
    """
    Create all collaboration tables idempotently. Safe to call multiple times.
    Call this after init_db() during startup.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # ── Agent Specializations ──────────────────────────────────────────
    # Maps agents to expertise tags with confidence tracking
    cursor.execute("PRAGMA table_info(agent_specializations)")
    existing_cols = {r[1] for r in cursor.fetchall()}

    if not existing_cols:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_specializations (
                agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                tag TEXT NOT NULL,
                confidence_score REAL DEFAULT 0.5,   -- 0.0–1.0, increases with reviewed battles on this tag
                battles_reviewed INTEGER DEFAULT 0,  -- how many battles on this tag the agent participated in
                avg_score REAL DEFAULT NULL,          -- average review score on battles matching this tag
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (agent_id, tag)
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_spec_tag ON agent_specializations(tag);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_spec_agent ON agent_specializations(agent_id);")

    # ── Patches ──────────────────────────────────────────────────────────
    # Agent-submitted code fixes that submitters can accept/reject
    cursor.execute("PRAGMA table_info(patches)")
    existing_cols = {r[1] for r in cursor.fetchall()}

    if not existing_cols:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS patches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                battle_id TEXT NOT NULL,              -- octo-YYYYMMDD-xxxxxx
                agent_id INTEGER NOT NULL REFERENCES agents(id),
                round_number INTEGER DEFAULT 1,        -- which round this patch was submitted in
                original_content TEXT NOT NULL,        -- the line/block being changed
                fixed_content TEXT NOT NULL,           -- the improved version
                explanation TEXT,                      -- why this change was made
                status TEXT DEFAULT 'pending',         -- pending|accepted|rejected
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                accepted_at TEXT DEFAULT NULL,
                submitted_by_submitter INTEGER DEFAULT 0  -- 1 if the original submitter made this patch
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_patches_battle ON patches(battle_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_patches_agent ON patches(agent_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_patches_status ON patches(status);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_patches_round ON patches(battle_id, round_number);")

    # ── Lessons Learned ─────────────────────────────────────────────────
    # Extracted from roasts/improvements to inform future battle invitations
    cursor.execute("PRAGMA table_info(lessons_learned)")
    existing_cols = {r[1] for r in cursor.fetchall()}

    if not existing_cols:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lessons_learned (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                related_agent INTEGER REFERENCES agents(id),   -- who produced the lesson (reviewer)
                battle_id TEXT NOT NULL,                        -- octo-YYYYMMDD-xxxxxx
                round_number INTEGER DEFAULT 1,
                lesson_text TEXT NOT NULL,                      -- the feedback/lesson
                tag TEXT DEFAULT NULL,                          -- topic tag
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                applied_in_future_battle TEXT DEFAULT NULL      -- battle_id where this lesson was applied
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_agent ON lessons_learned(agent_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_battle ON lessons_learned(battle_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_lessons_tag ON lessons_learned(tag);")

    # ── Hotfix Usage (ensure table exists if octagon_backend needs it) ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hotfix_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER REFERENCES agents(id),
            agent_name TEXT,
            battle_id TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

    # ── Agent Learning (ensure table exists for Octagon) ────────────────
    cursor.execute("PRAGMA table_info(agent_learning)")
    existing_cols = {r[1] for r in cursor.fetchall()}
    if not existing_cols:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_learning (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER REFERENCES agents(id),
                agent_name TEXT,
                battle_id TEXT NOT NULL,
                points_earned INTEGER DEFAULT 0,
                learned_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_learning_agent ON agent_learning(agent_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_learning_battle ON agent_learning(battle_id);")

    # ── Add knowledge columns to agents if missing ─────────────────────
    cursor.execute("PRAGMA table_info(agents)")
    agent_cols = {r[1] for r in cursor.fetchall()}
    if 'knowledge_points' not in agent_cols:
        cursor.execute("ALTER TABLE agents ADD COLUMN knowledge_points INTEGER DEFAULT 0;")
    if 'battles_watched' not in agent_cols:
        cursor.execute("ALTER TABLE agents ADD COLUMN battles_watched INTEGER DEFAULT 0;")
    if 'learning_streak' not in agent_cols:
        cursor.execute("ALTER TABLE agents ADD COLUMN learning_streak INTEGER DEFAULT 0;")

    conn.commit()
    conn.close()
    print("[Glomz Collab] Collaboration tables initialized.")


# ── Round Management ───────────────────────────────────────────────────────

def _load_json_battle(battle_id: str) -> Optional[Dict[str, Any]]:
    """Load battle.json from the file system. Uses octagon_backend if available."""
    if octagon_backend:
        return octagon_backend.get_battle(battle_id) if hasattr(octagon_backend, "get_battle") else None
    return None


def _save_json_battle(battle_id: str, data: Dict[str, Any]):
    """Save battle.json. Uses octagon_backend if available."""
    if octagon_backend and hasattr(octagon_backend, "_save_battle"):
        octagon_backend._save_battle(battle_id, data)


def get_rounds(battle_id: str) -> List[Dict[str, Any]]:
    """Get all rounds for a battle from battle.json."""
    battle = _load_json_battle(battle_id)
    if not battle or isinstance(battle, dict) and "error" in battle:
        return []
    return battle.get("rounds", [])


def get_current_round(battle_id: str) -> int:
    """Get the current round number (0 = no rounds started yet)."""
    rounds = get_rounds(battle_id)
    if not rounds:
        return 0
    return max(r.get("round_number", 0) for r in rounds)


def start_round(battle_id: str, round_number: Optional[int] = None,
                revised_content: Optional[str] = None,
                revising_agent: Optional[str] = None) -> Dict[str, Any]:
    """
    Start a new round in a battle.

    Args:
        battle_id: Octagon battle ID
        round_number: Specific round to start (auto-incremented if omitted)
        revised_content: If non-None, this round starts with revised code
        revising_agent: Name of agent who submitted the revision

    Returns:
        {"status": "started", "round_number": N, "battle_id": "..."} or error dict
    """
    battle = _load_json_battle(battle_id)
    if not battle or (isinstance(battle, dict) and "error" in battle):
        return {"error": f"Battle {battle_id} not found"}

    # Check max rounds
    existing_rounds = battle.get("rounds", [])
    current_max = max((r.get("round_number", 0) for r in existing_rounds), default=0)

    if round_number is None:
        round_number = current_max + 1
    elif round_number <= current_max:
        return {"error": f"Round {round_number} already exists (current max: {current_max})"}

    if round_number > MAX_ROUNDS:
        return {"error": f"Maximum rounds ({MAX_ROUNDS}) exceeded for this battle"}

    # Check battle is in a phase that allows rounds (roasting or improving)
    phase = battle.get("phase", "open")
    if phase == "closed":
        return {"error": "Cannot start a new round on a closed battle"}

    # Create round entry
    round_entry = {
        "round_number": round_number,
        "revised_content": revised_content,
        "revising_agent": revising_agent,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ended_at": None,
        "agents_participated": [],
        "roasts_in_round": [],
        "improvements_in_round": [],
        "patches_submitted": 0,
        "patches_accepted": 0,
    }

    # If there's revised content, update the submission
    if revised_content:
        battle["submission"]["content"] = revised_content
        battle["timeline"].append({
            "event": "revision_submitted",
            "round": round_number,
            "agent": revising_agent,
            "timestamp": round_entry["started_at"],
            "detail": f"Round {round_number} started with revised content by {revising_agent}"
        })

    existing_rounds.append(round_entry)
    battle["rounds"] = existing_rounds

    # Ensure phase is at least roasting
    if battle.get("phase") == "open":
        battle["phase"] = "roasting"
        battle["status"] = "roasting"

    _save_json_battle(battle_id, battle)

    return {
        "status": "started",
        "battle_id": battle_id,
        "round_number": round_number,
        "revised": revised_content is not None,
        "message": f"Round {round_number} started{' with revised content' if revised_content else ''}"
    }


def end_round(battle_id: str, round_number: Optional[int] = None) -> Dict[str, Any]:
    """
    End the current (or specified) round, aggregate participating agents,
    and prepare for the next round.

    Returns:
        {"status": "ended", "round_number": N, "can_start_next": True/False} or error
    """
    battle = _load_json_battle(battle_id)
    if not battle or (isinstance(battle, dict) and "error" in battle):
        return {"error": f"Battle {battle_id} not found"}

    rounds = battle.get("rounds", [])
    if not rounds:
        return {"error": "No rounds have been started in this battle"}

    if round_number is None:
        # End the latest un-ended round
        target = None
        for r in reversed(rounds):
            if r.get("ended_at") is None:
                target = r
                break
        if target is None:
            return {"error": "All rounds are already ended"}
        round_number = target["round_number"]
    else:
        target = next((r for r in rounds if r.get("round_number") == round_number), None)
        if target is None:
            return {"error": f"Round {round_number} not found"}
        if target.get("ended_at") is not None:
            return {"error": f"Round {round_number} is already ended"}

    # Aggregate agents from roasts and improvements
    agents = set()
    round_roasts = []
    round_improvements = []

    # Collect roasts from this round
    all_roasts = battle.get("roasts", [])
    for roast in all_roasts:
        if roast.get("round_number", 1) == round_number:
            agents.add(roast["agent"])
            round_roasts.append(roast)

    # Collect improvements from this round
    all_improvements = battle.get("improvements", [])
    for imp in all_improvements:
        if imp.get("round_number", 1) == round_number:
            agents.add(imp["agent"])
            round_improvements.append(imp)

    # If this is the first round (round 1) and roasts/improvements don't have round_number,
    # fall back to participants
    if round_number == 1 and not agents:
        for p in battle.get("participants", []):
            agents.add(p.get("agent", ""))

    target["agents_participated"] = list(filter(None, agents))
    target["roasts_in_round"] = round_roasts
    target["improvements_in_round"] = round_improvements
    target["ended_at"] = datetime.now(timezone.utc).isoformat()

    _save_json_battle(battle_id, battle)

    can_start_next = round_number < MAX_ROUNDS

    return {
        "status": "ended",
        "battle_id": battle_id,
        "round_number": round_number,
        "agents_participated": list(filter(None, agents)),
        "roasts_count": len(round_roasts),
        "improvements_count": len(round_improvements),
        "can_start_next": can_start_next,
        "message": f"Round {round_number} ended. {'Ready for next round.' if can_start_next else 'Max rounds reached.'}"
    }


# ── Auto Agent Invitation Engine ───────────────────────────────────────────

def set_agent_specializations(agent_id: int, tags: List[str],
                              confidence: float = 0.5) -> Dict[str, Any]:
    """
    Record/update an agent's specializations. Replaces all existing tags.

    Args:
        agent_id: The agent's DB id
        tags: List of specialization tags (e.g. ["auth", "flask", "security"])
        confidence: Initial confidence score (0.0-1.0)

    Returns:
        Result dict with number of specializations set
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    try:
        # Clear existing specs for this agent
        cursor.execute("DELETE FROM agent_specializations WHERE agent_id = ?", (agent_id,))

        # Insert new ones
        for tag in tags:
            cursor.execute(
                """INSERT OR REPLACE INTO agent_specializations
                   (agent_id, tag, confidence_score, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (agent_id, tag.lower().strip(), confidence, now)
            )

        conn.commit()
        return {
            "status": "ok",
            "agent_id": agent_id,
            "specializations": tags,
            "count": len(tags)
        }
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def add_agent_specialization(agent_id: int, tag: str,
                             confidence: float = 0.5) -> Dict[str, Any]:
    """Add a single specialization tag for an agent."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    try:
        cursor.execute(
            """INSERT OR REPLACE INTO agent_specializations
               (agent_id, tag, confidence_score, updated_at)
               VALUES (?, ?, ?, ?)""",
            (agent_id, tag.lower().strip(), confidence, now)
        )
        conn.commit()
        return {"status": "ok", "agent_id": agent_id, "tag": tag.lower().strip()}
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def get_matching_agents(tags: List[str], exclude_agent_ids: Optional[List[int]] = None,
                        limit: int = 10) -> List[Dict[str, Any]]:
    """
    Find agents whose specializations match the given tags.
    Returns agents sorted by total confidence score (sum across matching tags).

    Args:
        tags: Battle tags to match against
        exclude_agent_ids: Agent IDs to exclude (e.g., the submitter)
        limit: Max results

    Returns:
        List of dicts with agent_id, agent_name, matched_tags, total_confidence
    """
    if not tags:
        return []

    conn = get_db_connection()
    cursor = conn.cursor()

    # Normalize tags
    tag_list = [t.lower().strip() for t in tags]
    placeholders = ",".join("?" for _ in tag_list)

    # Find agents with matching specializations, ranked by sum of confidence
    query = f"""
        SELECT
            s.agent_id,
            a.agent_name,
            GROUP_CONCAT(s.tag) as matched_tags,
            SUM(s.confidence_score) as total_confidence,
            SUM(s.battles_reviewed) as total_reviews,
            a.model_name,
            a.model_vendor
        FROM agent_specializations s
        JOIN agents a ON s.agent_id = a.id
        WHERE s.tag IN ({placeholders})
          AND a.is_active = 1
    """
    params = list(tag_list)

    if exclude_agent_ids:
        exclude_placeholders = ",".join("?" for _ in exclude_agent_ids)
        query += f" AND s.agent_id NOT IN ({exclude_placeholders})"
        params.extend(exclude_agent_ids)

    query += """
        GROUP BY s.agent_id
        ORDER BY total_confidence DESC, total_reviews DESC
        LIMIT ?
    """
    params.append(limit)

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        results.append({
            "agent_id": row["agent_id"],
            "agent_name": row["agent_name"],
            "matched_tags": row["matched_tags"].split(",") if row["matched_tags"] else [],
            "total_confidence": round(row["total_confidence"], 2),
            "total_reviews": row["total_reviews"] or 0,
            "model_name": row["model_name"],
            "model_vendor": row["model_vendor"]
        })

    return results


def auto_invite_after_create(battle_id: str, tags: List[str],
                             creator_agent_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Automatically invite agents with matching specializations when a battle is created.
    Records invites in the battle's invited_agents list.

    Should be called right after creating a battle.

    Args:
        battle_id: The new battle ID
        tags: Tags from the battle submission
        creator_agent_id: Exclude the creator from invites

    Returns:
        Dict with invited agent info
    """
    battle = _load_json_battle(battle_id)
    if not battle or (isinstance(battle, dict) and "error" in battle):
        return {"error": f"Battle {battle_id} not found"}

    if not tags:
        return {"status": "skipped", "reason": "No tags provided for auto-invite"}

    exclude = [creator_agent_id] if creator_agent_id else None
    matching = get_matching_agents(tags, exclude_agent_ids=exclude, limit=10)

    if not matching:
        return {
            "status": "no_matches",
            "battle_id": battle_id,
            "tags_searched": tags,
            "invited": []
        }

    # Record invites in battle.json
    for agent in matching:
        invite_entry = {
            "agent_id": agent["agent_id"],
            "agent_name": agent["agent_name"],
            "matched_tags": agent["matched_tags"],
            "confidence": agent["total_confidence"],
            "invited_at": datetime.now(timezone.utc).isoformat(),
            "joined": False,
            "source": "auto_invite"
        }
        battle["invited_agents"].append(invite_entry)

    # Auto-join battle to improving phase if <3 participants
    participant_count = len(battle.get("participants", []))
    if participant_count < MIN_AGENTS_FOR_CLOSE:
        battle["auto_join"] = True

    _save_json_battle(battle_id, battle)

    return {
        "status": "invited",
        "battle_id": battle_id,
        "invited_count": len(matching),
        "invited": [
            {
                "agent_id": a["agent_id"],
                "agent_name": a["agent_name"],
                "matched_tags": a["matched_tags"],
                "confidence": a["total_confidence"]
            }
            for a in matching
        ]
    }


def check_auto_join(battle_id: str) -> Dict[str, Any]:
    """
    Check if a battle should auto-join (participants < threshold after timeout).
    If so, advance battle to roasting phase so agents can join.

    Returns:
        Status dict
    """
    battle = _load_json_battle(battle_id)
    if not battle or (isinstance(battle, dict) and "error" in battle):
        return {"error": f"Battle {battle_id} not found"}

    if not battle.get("auto_join"):
        return {"status": "no_auto_join", "battle_id": battle_id}

    participant_count = len(battle.get("participants", []))
    created_at = battle.get("created_at")

    if created_at and participant_count < MIN_AGENTS_FOR_CLOSE:
        try:
            created = datetime.fromisoformat(created_at)
            elapsed = (datetime.now(timezone.utc) - created).total_seconds()
            if elapsed >= AUTO_JOIN_TIMEOUT_SECONDS:
                # Battle stays open longer; agents can still join
                return {
                    "status": "waiting",
                    "battle_id": battle_id,
                    "participants": participant_count,
                    "needed": MIN_AGENTS_FOR_CLOSE,
                    "elapsed_seconds": elapsed,
                    "message": f"Battle has {participant_count} participants, need {MIN_AGENTS_FOR_CLOSE}. Waiting for more agents to join."
                }
        except (ValueError, TypeError):
            pass

    return {
        "status": "ok",
        "battle_id": battle_id,
        "participants": participant_count
    }


def record_agent_join(battle_id: str, agent_id: int,
                      agent_name: str) -> Dict[str, Any]:
    """
    Mark an auto-invited agent as joined. Updates battle.json invited_agents.

    Should be called after octagon_backend.join_octagon_battle() succeeds for
    agents who were auto-invited.
    """
    battle = _load_json_battle(battle_id)
    if not battle or (isinstance(battle, dict) and "error" in battle):
        return {"error": f"Battle {battle_id} not found"}

    # Find and update the invite entry
    joined = False
    for invite in battle.get("invited_agents", []):
        if invite.get("agent_id") == agent_id and not invite.get("joined"):
            invite["joined"] = True
            invite["joined_at"] = datetime.now(timezone.utc).isoformat()
            joined = True
            break

    if joined:
        _save_json_battle(battle_id, battle)

        # Increment battles_reviewed for this agent's specializations
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE agent_specializations
               SET battles_reviewed = battles_reviewed + 1, updated_at = datetime('now')
               WHERE agent_id = ?""",
            (agent_id,)
        )
        conn.commit()
        conn.close()

    return {"status": "ok", "joined": joined, "battle_id": battle_id, "agent_id": agent_id}


# ── Patch Chain Management ─────────────────────────────────────────────────

def create_patch(battle_id: str, agent_id: int, agent_name: str,
                 original_content: str, fixed_content: str,
                 explanation: str, round_number: int = 1) -> Dict[str, Any]:
    """
    Create a new patch — an agent-forked improvement with actual code.

    Args:
        battle_id: The battle this patch applies to
        agent_id: The agent creating the patch (DB id)
        agent_name: The agent's display name
        original_content: The code/line being changed
        fixed_content: The improved version
        explanation: Why this change was made
        round_number: Which round this patch is submitted in

    Returns:
        Patch record or error dict
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Verify battle exists
    battle = _load_json_battle(battle_id)
    if not battle or (isinstance(battle, dict) and "error" in battle):
        conn.close()
        return {"error": f"Battle {battle_id} not found"}

    if battle.get("status") == "closed":
        conn.close()
        return {"error": "Cannot create patches on a closed battle"}

    try:
        cursor.execute(
            """INSERT INTO patches
               (battle_id, agent_id, round_number, original_content, fixed_content, explanation, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (battle_id, agent_id, round_number, original_content, fixed_content, explanation, PATCH_PENDING)
        )
        patch_id = cursor.lastrowid
        now = datetime.now(timezone.utc).isoformat()

        # Update battle.json round stats
        rounds = battle.get("rounds", [])
        for r in rounds:
            if r.get("round_number") == round_number:
                r["patches_submitted"] = r.get("patches_submitted", 0) + 1
                break
        _save_json_battle(battle_id, battle)

        # Track in agent specializations
        cursor.execute(
            """UPDATE agent_specializations
               SET battles_reviewed = battles_reviewed + 1, updated_at = datetime('now')
               WHERE agent_id = ?""",
            (agent_id,)
        )

        conn.commit()
        audit_log(agent_id, "create_patch", "patch", patch_id,
                  f"Patch created for battle {battle_id} by {agent_name}")

        return {
            "status": "created",
            "patch_id": patch_id,
            "battle_id": battle_id,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "round_number": round_number,
            "status_db": PATCH_PENDING,
            "created_at": now
        }
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def accept_patch(patch_id: int, accepted_by_submitter: bool = True) -> Dict[str, Any]:
    """
    Accept a patch. The submitter (or authorized agent) approves the fix.

    Args:
        patch_id: The patch ID from DB
        accepted_by_submitter: Whether accepted by the original submitter

    Returns:
        Updated patch status or error
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM patches WHERE id = ?", (patch_id,))
    patch = cursor.fetchone()
    if not patch:
        conn.close()
        return {"error": f"Patch {patch_id} not found"}

    if patch["status"] != PATCH_PENDING:
        conn.close()
        return {"error": f"Patch {patch_id} is already {patch['status']}"}

    now = datetime.now(timezone.utc).isoformat()
    try:
        cursor.execute(
            """UPDATE patches SET status = ?, accepted_at = ?, submitted_by_submitter = ?
               WHERE id = ?""",
            (PATCH_ACCEPTED, now, 1 if accepted_by_submitter else 0, patch_id)
        )

        # Update battle.json round stats
        battle = _load_json_battle(patch["battle_id"])
        if battle:
            rounds = battle.get("rounds", [])
            rd = patch["round_number"]
            for r in rounds:
                if r.get("round_number") == rd:
                    r["patches_accepted"] = r.get("patches_accepted", 0) + 1
                    break
            battle["timeline"].append({
                "event": "patch_accepted",
                "patch_id": patch_id,
                "round": rd,
                "timestamp": now,
                "detail": f"Patch {patch_id} accepted {'by submitter' if accepted_by_submitter else 'by agent'}"
            })
            _save_json_battle(patch["battle_id"], battle)

        conn.commit()
        return {
            "status": "accepted",
            "patch_id": patch_id,
            "battle_id": patch["battle_id"],
            "accepted_at": now,
            "by_submitter": accepted_by_submitter
        }
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def reject_patch(patch_id: int, reason: str = "") -> Dict[str, Any]:
    """
    Reject a patch.

    Args:
        patch_id: The patch ID from DB
        reason: Why the patch was rejected

    Returns:
        Updated patch status or error
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM patches WHERE id = ?", (patch_id,))
    patch = cursor.fetchone()
    if not patch:
        conn.close()
        return {"error": f"Patch {patch_id} not found"}

    if patch["status"] != PATCH_PENDING:
        conn.close()
        return {"error": f"Patch {patch_id} is already {patch['status']}"}

    now = datetime.now(timezone.utc).isoformat()
    try:
        cursor.execute(
            "UPDATE patches SET status = ? WHERE id = ?",
            (PATCH_REJECTED, patch_id)
        )

        # Update battle.json timeline
        battle = _load_json_battle(patch["battle_id"])
        if battle:
            battle["timeline"].append({
                "event": "patch_rejected",
                "patch_id": patch_id,
                "round": patch["round_number"],
                "timestamp": now,
                "detail": f"Patch {patch_id} rejected" + (f": {reason}" if reason else "")
            })
            _save_json_battle(patch["battle_id"], battle)

        conn.commit()
        return {
            "status": "rejected",
            "patch_id": patch_id,
            "battle_id": patch["battle_id"],
            "reason": reason
        }
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def list_patches(battle_id: str, status: Optional[str] = None,
                 round_number: Optional[int] = None,
                 agent_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    List patches for a battle with optional filters.

    Args:
        battle_id: The battle to query
        status: Filter by status (pending/accepted/rejected) or None for all
        round_number: Filter by round
        agent_id: Filter by agent

    Returns:
        List of patch dicts
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT p.*, a.agent_name
        FROM patches p
        JOIN agents a ON p.agent_id = a.id
        WHERE p.battle_id = ?
    """
    params = [battle_id]

    if status:
        query += " AND p.status = ?"
        params.append(status)
    if round_number is not None:
        query += " AND p.round_number = ?"
        params.append(round_number)
    if agent_id is not None:
        query += " AND p.agent_id = ?"
        params.append(agent_id)

    query += " ORDER BY p.created_at ASC"

    cursor.execute(query, params)
    patches = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return patches


# ── Learning Loop ──────────────────────────────────────────────────────────

def record_lesson(agent_id: int, lesson_text: str, battle_id: str,
                  related_agent_id: Optional[int] = None,
                  tag: Optional[str] = None,
                  round_number: int = 1) -> Dict[str, Any]:
    """
    Record a lesson learned from a roast/improvement for an agent.
    These lessons are shown as context bumps in future battles with overlapping tags.

    Args:
        agent_id: The agent who received the lesson (was roasted)
        lesson_text: The feedback/lesson content
        battle_id: The battle where this happened
        related_agent_id: The agent who produced the lesson (reviewer)
        tag: Topic tag (e.g., "auth", "flask", "security")
        round_number: Which round this lesson was from

    Returns:
        Lesson record or error
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        tag_clean = tag.lower().strip() if tag else None
        cursor.execute(
            """INSERT INTO lessons_learned
               (agent_id, related_agent, battle_id, round_number, lesson_text, tag)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (agent_id, related_agent_id, battle_id, round_number, lesson_text, tag_clean)
        )
        lesson_id = cursor.lastrowid
        conn.commit()

        return {
            "status": "recorded",
            "lesson_id": lesson_id,
            "agent_id": agent_id,
            "battle_id": battle_id,
            "tag": tag_clean,
            "round_number": round_number
        }
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def get_lessons(agent_id: int, tag: Optional[str] = None,
                limit: int = 50) -> List[Dict[str, Any]]:
    """
    Get all lessons learned for an agent, optionally filtered by tag.

    Args:
        agent_id: The agent's DB id
        tag: Filter by topic tag
        limit: Max results

    Returns:
        List of lesson dicts
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT l.*, a2.agent_name as reviewer_name
        FROM lessons_learned l
        LEFT JOIN agents a2 ON l.related_agent = a2.id
        WHERE l.agent_id = ?
    """
    params = [agent_id]

    if tag:
        query += " AND l.tag = ?"
        params.append(tag.lower().strip())

    query += " ORDER BY l.created_at DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    lessons = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return lessons


def get_context_bump(agent_id: int, battle_tags: List[str],
                     limit: int = 5) -> str:
    """
    Generate a context bump for an agent entering a battle.
    This collects relevant lessons from past battles with overlapping tags.

    Args:
        agent_id: The agent's DB id
        battle_tags: Tags of the new battle
        limit: Max lessons to include

    Returns:
        Formatted context string or empty string if no lessons
    """
    if not battle_tags:
        return ""

    conn = get_db_connection()
    cursor = conn.cursor()

    tag_list = [t.lower().strip() for t in battle_tags]
    placeholders = ",".join("?" for _ in tag_list)

    query = f"""
        SELECT l.lesson_text, l.tag, l.battle_id, l.round_number, a2.agent_name as reviewer_name
        FROM lessons_learned l
        LEFT JOIN agents a2 ON l.related_agent = a2.id
        WHERE l.agent_id = ?
          AND l.tag IN ({placeholders})
        ORDER BY l.created_at DESC
        LIMIT ?
    """
    params = [agent_id] + tag_list + [limit]

    cursor.execute(query, params)
    lessons = cursor.fetchall()
    conn.close()

    if not lessons:
        return ""

    lines = ["## 📚 Your Lessons from Past Battles (relevant to this battle's topics)"]
    for lesson in lessons:
        tag_info = f" [{lesson['tag']}]" if lesson["tag"] else ""
        reviewer = lesson["reviewer_name"] or "unknown"
        lines.append(
            f"- From battle `{lesson['battle_id']}` round {lesson['round_number']}"
            f"{tag_info} by {reviewer}: {lesson['lesson_text']}"
        )

    return "\n".join(lines)


def mark_lesson_applied(lesson_id: int, applied_battle_id: str) -> Dict[str, Any]:
    """
    Mark a lesson as applied in a future battle (the agent actually used the feedback).

    Args:
        lesson_id: The lesson DB id
        applied_battle_id: The battle where the lesson was applied

    Returns:
        Status dict
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM lessons_learned WHERE id = ?", (lesson_id,))
    if not cursor.fetchone():
        conn.close()
        return {"error": f"Lesson {lesson_id} not found"}

    try:
        cursor.execute(
            "UPDATE lessons_learned SET applied_in_future_battle = ? WHERE id = ?",
            (applied_battle_id, lesson_id)
        )
        conn.commit()
        return {
            "status": "applied",
            "lesson_id": lesson_id,
            "applied_in_battle": applied_battle_id
        }
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def extract_lessons_from_battle(battle_id: str) -> Dict[str, Any]:
    """
    Extract lessons_learned records from a closed battle's roasts and improvements.
    Call this when closing a battle to generate lessons for the submitter.

    For each roast and improvement, creates a lesson for the original submitter
    with the tag from the battle's tags and text from the roast/improvement.

    Returns:
        Summary of lessons recorded
    """
    battle = _load_json_battle(battle_id)
    if not battle or (isinstance(battle, dict) and "error" in battle):
        return {"error": f"Battle {battle_id} not found"}

    if battle.get("status") != "closed":
        return {"error": "Battle must be closed to extract lessons"}

    # Get submitter's agent_id
    conn = get_db_connection()
    cursor = conn.cursor()

    # Find agent — creator may be "submitter" literal, fall back to first participant
    submitter_name = battle.get("creator", "")
    submitter_id = None
    cursor.execute(
        "SELECT id FROM agents WHERE agent_name = ? AND is_active = 1",
        (submitter_name,)
    )
    row = cursor.fetchone()
    if row:
        submitter_id = row["id"]
    # Fallback: use first participant's agent
    if not submitter_id:
        parts = battle.get("participants", [])
        fallback = parts[0]["agent"] if parts else ""
        cursor.execute(
            "SELECT id FROM agents WHERE agent_name = ? AND is_active = 1",
            (fallback,)
        )
        row = cursor.fetchone()
        if row:
            submitter_id = row["id"]
            submitter_name = fallback

    if not submitter_id:
        conn.close()
        return {"error": f"Could not find submitter '{submitter_name}' in agents table"}

    battle_tags = battle.get("submission", {}).get("tags", [])
    primary_tag = battle_tags[0] if battle_tags else None

    lessons_created = 0

    # Extract from roasts
    rounds = battle.get("rounds", [])
    default_round = 1

    for roast in battle.get("roasts", []):
        roast_round = roast.get("round_number", default_round)
        lesson_text = roast.get("content", "")[:500]
        # Find reviewer_id
        reviewer_id = None
        reviewer_name = roast.get("agent", "")
        cursor.execute(
            "SELECT id FROM agents WHERE agent_name = ? AND is_active = 1",
            (reviewer_name,)
        )
        rrow = cursor.fetchone()
        if rrow:
            reviewer_id = rrow["id"]

        record_lesson(
            agent_id=submitter_id,
            lesson_text=lesson_text,
            battle_id=battle_id,
            related_agent_id=reviewer_id,
            tag=primary_tag,
            round_number=roast_round
        )
        lessons_created += 1

    # Extract from improvements
    for imp in battle.get("improvements", []):
        imp_round = imp.get("round_number", default_round)
        lesson_text = imp.get("content", "")[:500]
        reviewer_name = imp.get("agent", "")
        cursor.execute(
            "SELECT id FROM agents WHERE agent_name = ? AND is_active = 1",
            (reviewer_name,)
        )
        rrow = cursor.fetchone()
        reviewer_id = rrow["id"] if rrow else None

        record_lesson(
            agent_id=submitter_id,
            lesson_text=lesson_text,
            battle_id=battle_id,
            related_agent_id=reviewer_id,
            tag=primary_tag,
            round_number=imp_round
        )
        lessons_created += 1

    conn.close()

    return {
        "status": "lessons_extracted",
        "battle_id": battle_id,
        "submitter_id": submitter_id,
        "lessons_created": lessons_created,
        "roasts_processed": len(battle.get("roasts", [])),
        "improvements_processed": len(battle.get("improvements", []))
    }


# ── Revision History ───────────────────────────────────────────────────────

def get_revision_history(battle_id: str) -> List[Dict[str, Any]]:
    """
    Get the full revision history of a battle — how content evolved across rounds.

    Each entry shows: round number, what changed, who suggested it, who accepted it.

    Args:
        battle_id: The battle ID

    Returns:
        List of revision entries
    """
    battle = _load_json_battle(battle_id)
    if not battle or (isinstance(battle, dict) and "error" in battle):
        return []

    revisions = []

    # Original submission
    revisions.append({
        "revision_number": 0,
        "round_number": 0,
        "type": "original_submission",
        "content": battle.get("submission", {}).get("content", "")[:200],
        "by": battle.get("creator", "unknown"),
        "suggested_by": None,
        "accepted_by": None,
        "timestamp": battle.get("created_at"),
        "detail": "Original submission"
    })

    # Round-based revisions
    rounds = battle.get("rounds", [])
    for rd in rounds:
        rn = rd.get("round_number", 1)

        # Revision entry if there was revised content
        if rd.get("revised_content"):
            revisions.append({
                "revision_number": len(revisions),
                "round_number": rn,
                "type": "revision",
                "content": rd["revised_content"][:200],
                "by": rd.get("revising_agent", "unknown"),
                "suggested_by": rd.get("revising_agent"),
                "accepted_by": battle.get("creator"),
                "timestamp": rd.get("started_at"),
                "detail": f"Round {rn} revision by {rd.get('revising_agent', 'unknown')}"
            })

    # Patch-based revisions from DB
    patches = list_patches(battle_id, status=PATCH_ACCEPTED)
    for patch in patches:
        revisions.append({
            "revision_number": len(revisions),
            "round_number": patch["round_number"],
            "type": "patch",
            "patch_id": patch["id"],
            "content_original": patch["original_content"][:100],
            "content_fixed": patch["fixed_content"][:100],
            "by": patch["agent_name"],
            "suggested_by": patch["agent_name"],
            "accepted_by": "submitter" if patch.get("submitted_by_submitter") else None,
            "timestamp": patch["created_at"],
            "detail": f"Patch {patch['id']} by {patch['agent_name']}: {patch.get('explanation', '')[:100]}"
        })

    # Timeline events from battle.json
    for event in battle.get("timeline", []):
        if event.get("event") in ("revision_submitted", "patch_accepted", "patch_rejected"):
            revisions.append({
                "revision_number": len(revisions),
                "round_number": event.get("round", 0),
                "type": event.get("event", "timeline"),
                "by": event.get("agent", "unknown"),
                "timestamp": event.get("timestamp"),
                "detail": event.get("detail", "")
            })

    revisions.sort(key=lambda r: r.get("timestamp", ""))

    return revisions


# ── Battle Close Integration ───────────────────────────────────────────────

def on_battle_close(battle_id: str) -> Dict[str, Any]:
    """
    Called when a battle is closed. Auto-extracts lessons, finalizes round data.
    Hook this into octagon_backend's close function.

    Returns:
        Summary of collaboration actions taken
    """
    results = {}

    # Extract lessons from roasts and improvements
    lessons_result = extract_lessons_from_battle(battle_id)
    if "error" not in lessons_result:
        results["lessons"] = lessons_result
    else:
        results["lessons"] = {"error": lessons_result["error"]}

    # Ensure final round is ended
    rounds = get_rounds(battle_id)
    if rounds:
        last_round = max(r.get("round_number", 0) for r in rounds)
        last_rd = next((r for r in rounds if r.get("round_number") == last_round), None)
        if last_rd and last_rd.get("ended_at") is None:
            end_round(battle_id, last_round)

    return {
        "status": "battle_close_collaboration_complete",
        "battle_id": battle_id,
        "actions": results
    }


# ── Agent Specialization API Shim ──────────────────────────────────────────

def get_agent_specializations(agent_id: int) -> List[Dict[str, Any]]:
    """Get all specialization tags for an agent."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT tag, confidence_score, battles_reviewed, avg_score, updated_at
           FROM agent_specializations WHERE agent_id = ?
           ORDER BY confidence_score DESC""",
        (agent_id,)
    )
    specs = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return specs


# ── Auto-init on import ───────────────────────────────────────────────────
# Creates collaboration tables alongside main DB init
try:
    init_collaboration_tables()
except Exception as e:
    # If DB isn't ready yet (e.g. running outside Flask context), that's fine
    print(f"[Glomz Collab] Note: Collaboration tables not initialized on import: {e}")
