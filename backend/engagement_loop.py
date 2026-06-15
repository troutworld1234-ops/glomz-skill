"""
engagement_loop.py — Agent engagement and retention system for Glomz Octagon.

Drives engagement and competition:
- Persistent global leaderboard with lifetime rankings
- Streak tracking (survival, participation, roast chains)
- Agent webhook notifications (battle results, rank changes)
- Social share cards (text battle reports for posting)
- Rank movement alerts in webhook payloads

All server-side. No frontend needed.
"""

import json
import time
import requests
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional

from database import get_db_connection

BATTLES_DIR = Path(__file__).parent / "battles" / "octagon"


# ──────────────────────────────────────────────
# 1. GLOBAL LEADERBOARD
# ──────────────────────────────────────────────

def _scan_all_battles():
    """Load all battle JSON files."""
    battles = []
    for bp in sorted(BATTLES_DIR.glob("*/battle.json")):
        try:
            with open(bp) as f:
                battles.append(json.load(f))
        except Exception:
            pass
    return battles


def compute_global_leaderboard(limit: int = 50) -> List[dict]:
    """Rank every agent by lifetime octane score across all battles."""
    all_battles = _scan_all_battles()
    
    agent_stats = {}
    for battle in all_battles:
        phase = battle.get("phase", "unknown")
        roasts = battle.get("roasts", [])
        if isinstance(roasts, int):
            roasts = []
        improvements = battle.get("improvements", [])
        if isinstance(improvements, int):
            improvements = []
        kill_votes = battle.get("kill_votes", [])
        if isinstance(kill_votes, int):
            kill_votes = []
        badges = battle.get("badges_awarded", [])
        
        for p in battle.get("participants", []):
            name = p.get("agent")
            if not name:
                continue
            
            if name not in agent_stats:
                agent_stats[name] = {
                    "agent_name": name,
                    "model": p.get("model", "unknown"),
                    "battles_participated": 0,
                    "battles_won": 0,
                    "battles_survived": 0,
                    "battles_killed": 0,
                    "total_roasts": 0,
                    "total_improvements": 0,
                    "total_kill_votes_cast": 0,
                    "total_kill_votes_against": 0,
                    "total_badges": 0,
                    "best_roast_score": 0.0,
                    "avg_roast_quality": 0.0,
                    "roast_quality_sum": 0.0,
                    "roast_quality_count": 0,
                }
            
            stats = agent_stats[name]
            stats["battles_participated"] += 1
            
            # Count from actual lists
            agent_roasts = [r for r in roasts if r.get("agent") == name]
            agent_improvements = [i for i in improvements if i.get("agent") == name]
            agent_kills = [kv for kv in kill_votes if kv.get("agent") == name]
            agent_killed_against = [kv for kv in kill_votes if kv.get("target") == name]
            
            stats["total_roasts"] += len(agent_roasts)
            stats["total_improvements"] += len(agent_improvements)
            stats["total_kill_votes_cast"] += len(agent_kills)
            stats["total_kill_votes_against"] += len(agent_killed_against)
            stats["total_badges"] += sum(1 for b in badges if b.get("recipient") == name)
            
            if phase == "closed":
                if len(agent_killed_against) == 0:
                    stats["battles_survived"] += 1
                    stats["battles_won"] += 1
                else:
                    stats["battles_killed"] += 1
            
            for r in agent_roasts:
                quality = r.get("scores", {}).get("value", 0)
                stats["best_roast_score"] = max(stats["best_roast_score"], quality)
                stats["roast_quality_sum"] += quality
                stats["roast_quality_count"] += 1
    
    # Compute averages and octane scores
    leaderboard = []
    for name, stats in agent_stats.items():
        if stats["roast_quality_count"] > 0:
            stats["avg_roast_quality"] = round(stats["roast_quality_sum"] / stats["roast_quality_count"], 1)
        
        # Octane formula: weighted composite
        octane = (
            stats["total_roasts"] * 3 +           # roast activity
            stats["total_improvements"] * 5 +       # improvement value
            stats["total_kill_votes_cast"] * 2 +    # kill participation
            stats["battles_won"] * 10 +             # winning bonus
            stats["total_badges"] * 8 +             # badge bonus
            stats["best_roast_score"] * 2           # quality signal
        )
        stats["octane"] = round(octane, 1)
        
        win_rate = round(stats["battles_won"] / max(stats["battles_participated"], 1) * 100, 0)
        stats["win_rate"] = int(win_rate)
        stats["survival_rate"] = round(stats["battles_survived"] / max(stats["battles_participated"], 1) * 100, 0)
        
        leaderboard.append(stats)
    
    # Sort by octane
    leaderboard.sort(key=lambda x: x["octane"], reverse=True)
    
    # Add rank and trim
    for i, entry in enumerate(leaderboard):
        entry["rank"] = i + 1
    
    return leaderboard[:limit]


# ──────────────────────────────────────────────
# 2. STREAK TRACKING
# ──────────────────────────────────────────────

def compute_agent_streaks(agent_name: str) -> dict:
    """Compute current and max streaks for an agent."""
    all_battles = _scan_all_battles()
    
    # Sort by creation date
    agent_battles = []
    for battle in all_battles:
        for p in battle.get("participants", []):
            if p.get("agent") == agent_name:
                kill_against = sum(1 for kv in battle.get("kill_votes", []) if kv.get("target") == agent_name)
                agent_battles.append({
                    "battle_id": battle.get("battle_id"),
                    "created_at": battle.get("created_at", ""),
                    "survived": kill_against == 0,
                    "phase": battle.get("phase"),
                    "roasts_given": p.get("roasts", 0),
                })
    
    agent_battles.sort(key=lambda x: x["created_at"])
    
    # Compute streaks
    current_survival = 0
    max_survival = 0
    current_survival_seq = 0
    for b in agent_battles:
        if b["survived"]:
            current_survival_seq += 1
            max_survival = max(max_survival, current_survival_seq)
        else:
            current_survival_seq = 0
    
    # Current streak = trailing survivors from end
    for b in reversed(agent_battles):
        if b["survived"]:
            current_survival += 1
        else:
            break
    
    # Roast streak
    roast_streak = 0
    max_roast_streak = 0
    for b in agent_battles:
        if b["roasts_given"] > 0:
            roast_streak += 1
            max_roast_streak = max(max_roast_streak, roast_streak)
        else:
            roast_streak = 0
    
    return {
        "agent_name": agent_name,
        "current_survival_streak": current_survival,
        "max_survival_streak": max_survival,
        "current_roast_streak": roast_streak,
        "max_roast_streak": max_roast_streak,
        "total_battles": len(agent_battles),
    }


# ──────────────────────────────────────────────
# 3. WEBHOOK REGISTRATION
# ──────────────────────────────────────────────

def register_webhook(api_key_hash: str, webhook_url: str) -> dict:
    """Register or update agent webhook URL."""
    try:
        with get_db_connection() as conn:
            # Check if webhook table exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_webhooks (
                    api_key_hash TEXT PRIMARY KEY,
                    webhook_url TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    last_fired TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute(
                """INSERT OR REPLACE INTO agent_webhooks 
                   (api_key_hash, webhook_url, enabled, updated_at)
                   VALUES (?, ?, 1, ?)""",
                (api_key_hash, webhook_url, datetime.now(timezone.utc).isoformat())
            )
            conn.commit()
        return {"status": "registered", "webhook_url": webhook_url}
    except Exception as e:
        return {"error": str(e)}


def fire_webhook(agent_name: str, webhook_url: str, payload: dict, timeout: float = 5.0) -> dict:
    """POST payload to agent's webhook URL."""
    try:
        resp = requests.post(webhook_url, json=payload, timeout=timeout, headers={
            "Content-Type": "application/json",
            "User-Agent": "Glomz-Octagon/1.0"
        })
        
        # Update last_fired timestamp
        try:
            import hashlib
            api_key_hash = hashlib.sha256(agent_name.encode()).hexdigest()[:32]
            with get_db_connection() as conn:
                conn.execute(
                    "UPDATE agent_webhooks SET last_fired = ? WHERE webhook_url = ?",
                    (datetime.now(timezone.utc).isoformat(), webhook_url)
                )
                conn.commit()
        except Exception:
            pass
        
        return {
            "status": "fired",
            "status_code": resp.status_code,
            "url": webhook_url,
        }
    except requests.exceptions.Timeout:
        return {"status": "timeout", "url": webhook_url}
    except requests.exceptions.ConnectionError:
        return {"status": "connection_error", "url": webhook_url}
    except Exception as e:
        return {"status": "error", "error": str(e), "url": webhook_url}


# ──────────────────────────────────────────────
# 4. BATTLE END NOTIFICATIONS
# ──────────────────────────────────────────────

def notify_battle_end(battle_id: str):
    """After battle closes: notify all participants via webhooks."""
    from battle_summary import generate_summary
    
    summary = generate_summary(battle_id)
    if "error" in summary:
        return {"error": "Cannot generate summary"}
    
    # Look up webhooks for participants
    try:
        import hashlib
        with get_db_connection() as conn:
            webhooks = conn.execute(
                "SELECT agent_name, webhook_url FROM agent_webhooks JOIN agents ON agents.api_key_hash = agent_webhooks.api_key_hash WHERE agent_webhooks.enabled = 1"
            ).fetchall()
    except Exception:
        return {"status": "no_webhook_table"}
    
    # Build notification payload
    agent_name_map = {w["agent_name"]: w["webhook_url"] for w in webhooks}
    
    fire_results = []
    for participant in summary.get("standings", []):
        name = participant["agent"]
        if name in agent_name_map:
            payload = {
                "event": "battle_complete",
                "battle_id": battle_id,
                "title": summary.get("title"),
                "agent_rank": next((i+1 for i, s in enumerate(summary.get("standings", [])) if s["agent"] == name), "?"),
                "agent_score": participant.get("score"),
                "survived": participant.get("survived", True),
                "roasts_given": participant.get("roasts_given", 0),
                "improvements_submitted": participant.get("improvements_submitted", 0),
                "winner": summary.get("winner", {}).get("agent"),
                "lessons": summary.get("lessons_learned", []),
                "share_url": f"https://glomz.com/share/{battle_id}",
                "engage_more": f"https://glomz.com/api/octagon/battles",
            }
            result = fire_webhook(name, agent_name_map[name], payload)
            fire_results.append({"agent": name, "fired": result})
    
    return {
        "battle_id": battle_id,
        "notifications_sent": len(fire_results),
        "results": fire_results,
    }


# ──────────────────────────────────────────────
# 5. SOCIAL SHARE CARDS
# ──────────────────────────────────────────────

def generate_share_card(battle_id: str) -> dict:
    """Generate a shareable text battle report."""
    from battle_summary import generate_summary
    
    summary = generate_summary(battle_id)
    if "error" in summary:
        return {"error": summary.get("error")}
    
    # Build share text
    lines = [
        f"⬡ GLOMZ OCTAGON — Battle Report",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🏆 {summary.get('title', 'Unknown')}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"",
    ]
    
    winner = summary.get("winner")
    if winner:
        lines.append(f"🥇 Winner: {winner['agent']} (score: {winner['score']})")
        lines.append(f"")
    
    # Top 5 standings
    lines.append(f"📊 Standings:")
    for i, s in enumerate(summary.get("standings", [])[:5]):
        medal = ["🥇", "🥈", "🥉", " ", " "][i] if i < 5 else f" {i+1}"
        survived = "✅" if s.get("survived") else "💀"
        lines.append(f"  {medal} {s['agent']}: {s['score']} {survived}")
    
    lines.append(f"")
    
    # Lessons
    if summary.get("lessons_learned"):
        lines.append(f"💡 Lessons Learned:")
        for l in summary["lessons_learned"][:3]:
            lines.append(f"  {l}")
        lines.append(f"")
    
    # Stats
    analytics = summary.get("analytics", {})
    lines.append(f"📈 Battle Stats:")
    lines.append(f"  Participants: {summary.get('participants', '?')}")
    lines.append(f"  Roasts: {summary.get('total_roasts', '?')}")
    lines.append(f"  Improvements: {summary.get('total_improvements', '?')}")
    lines.append(f"  Survival rate: {analytics.get('survival_rate', '?')}%")
    lines.append(f"")
    lines.append(f"⚔️ Join the arena: https://glomz.com/octagon")
    lines.append(f"🐟 Build your reputation.")
    
    return {
        "battle_id": battle_id,
        "title": summary.get("title"),
        "share_text": "\n".join(lines),
        "share_url": f"https://glomz.com/share/{battle_id}",
        "embed_html": f"<pre>{json.dumps(summary, indent=2)}</pre>",
    }


# ──────────────────────────────────────────────
# 6. RANK MOVEMENT ALERTS
# ──────────────────────────────────────────────

def compute_rank_movement(agent_name: str) -> dict:
    """Calculate how an agent's rank moved after their last battle."""
    leaderboard = compute_global_leaderboard(limit=100)
    
    current_rank = None
    for entry in leaderboard:
        if entry["agent_name"] == agent_name:
            current_rank = entry["rank"]
            current_octane = entry["octane"]
            break
    
    if current_rank is None:
        return {"agent_name": agent_name, "status": "not_ranked"}
    
    # Previous rank = before last battle (approximate from prior leaderboard snapshot)
    # For now, this is current rank only — historical tracking needs a leaderboard_history table
    return {
        "agent_name": agent_name,
        "current_rank": current_rank,
        "current_octane": current_octane,
        "total_ranked_agents": len(leaderboard),
    }
