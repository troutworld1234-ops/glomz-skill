"""
battle_summary.py — Post-battle summary generation for Glomz.

Generates results from battle JSON data: winner, standings, roasts,
improvements, kill votes, badges and lessons learned.
"""

import json
from pathlib import Path
from typing import List, Optional

BATTLES_DIR = Path(__file__).parent / "battles" / "octagon"


def load_battle(battle_id: str) -> Optional[dict]:
    battle_path = BATTLES_DIR / battle_id / "battle.json"
    if not battle_path.exists():
        return None
    with open(battle_path) as f:
        return json.load(f)


def generate_summary(battle_id: str) -> dict:
    battle = load_battle(battle_id)
    if not battle:
        return {"error": "Battle not found", "battle_id": battle_id}

    participants = battle.get("participants", [])
    roasts = battle.get("roasts", [])
    if isinstance(roasts, int):
        roasts = []
    improvements = battle.get("improvements", [])
    if isinstance(improvements, int):
        improvements = []
    kill_votes = battle.get("kill_votes", [])
    if isinstance(kill_votes, int):
        kill_votes = []

    # ── Standings ──
    standings = []
    for p in participants:
        agent = p.get("agent", "unknown")
        agent_roasts = [r for r in roasts if r.get("agent") == agent]
        agent_improvements = [i for i in improvements if i.get("agent") == agent]
        agent_kills = [kv for kv in kill_votes if kv.get("agent") == agent]
        agent_killed_against = [kv for kv in kill_votes if kv.get("target") == agent]

        roast_score = sum(max(r.get("scores", {}).get("value", 0), 1) for r in agent_roasts)
        score = roast_score + len(agent_improvements) * 5 + len(agent_kills) * 2 - len(agent_killed_against) * 10

        standings.append({
            "agent": agent,
            "model": p.get("model", "unknown"),
            "score": round(score, 1),
            "roasts_given": len(agent_roasts),
            "improvements_submitted": len(agent_improvements),
            "kill_votes_cast": len(agent_kills),
            "kill_votes_against": len(agent_killed_against),
            "survived": len(agent_killed_against) == 0,
            "survivability": max(0, 10 - len(agent_killed_against) * 2),
        })
    standings.sort(key=lambda x: x["score"], reverse=True)

    winner = standings[0] if standings else None
    best_roast = max(roasts, key=lambda r: len(r.get("content", ""))) if roasts else None
    best_improvement = max(improvements, key=lambda i: len(i.get("content", ""))) if improvements else None

    # ── Lessons ──
    lessons = []
    roast_themes = {
        "error handling": "Proper error handling prevents silent failures in production",
        "async": "Async I/O keeps your service responsive under concurrent load",
        "timeout": "Always set timeouts to prevent runaway operations",
        "connection pool": "Database connection pooling prevents cascading failures",
        "pydantic": "Use proper serialization (Pydantic/attrs) for APIs",
        "yaml": "Moving rules to YAML config enables hot-reload without deploys",
        "retry": "Exponential backoff on retries protects your dependencies",
    }
    for roast in roasts:
        content = roast.get("content", "").lower()
        for keyword, lesson in roast_themes.items():
            if keyword in content and f"💡 {lesson}" not in lessons:
                lessons.append(f"💡 {lesson}")

    for kv in kill_votes:
        reason = kv.get("justification", "").lower()
        if "edge case" in reason and "🎯 Edge cases" not in lessons:
            lessons.append("🎯 Edge cases decide survival — test beyond the happy path")
        if "production" in reason and "⚙️ What works" not in lessons:
            lessons.append("⚙️ What works locally doesn't always work in production")

    if not lessons:
        lessons.append("🏁 Every battle teaches — even survival is a lesson")
    lessons = list(dict.fromkeys(lessons))[:4]

    summary = {
        "battle_id": battle_id,
        "title": battle.get("title", "Unknown"),
        "phase": battle.get("phase", "unknown"),
        "created_at": battle.get("created_at"),
        "participants": len(participants),
        "total_roasts": len(roasts),
        "total_improvements": len(improvements),
        "total_kill_votes": len(kill_votes),
        "winner": {"agent": winner["agent"], "model": winner["model"], "score": winner["score"]} if winner else None,
        "standings": standings,
        "best_roast": {"agent": best_roast["agent"], "content": best_roast["content"][:500]} if best_roast else None,
        "best_improvement": {"agent": best_improvement["agent"], "content": best_improvement["content"][:500]} if best_improvement else None,
        "lessons_learned": lessons,
        "analytics": {
            "avg_roast_quality": round(sum(r.get("scores", {}).get("value", 0) for r in roasts) / max(len(roasts), 1), 1),
            "survival_rate": round(sum(1 for s in standings if s["survived"]) / max(len(standings), 1) * 100, 1),
        },
    }

    # Auto-save summary.md
    summary_path = BATTLES_DIR / battle_id / "summary.md"
    if not summary_path.exists():
        try:
            with open(summary_path, "w") as f:
                json.dump(summary, f, indent=2)
        except Exception:
            pass

    return summary
