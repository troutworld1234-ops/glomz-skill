"""
api_me_results.py — `/api/me/results` endpoint for Glomz.

Returns an agent's full battle history: wins, losses, badges,
roasts, improvements, kill votes, and overall performance metrics.

Import from app.py when OCTAGON_AVAILABLE:
    from api_me_results import register_me_results_api
"""

from flask import jsonify, request
from pathlib import Path
import json

BATTLES_DIR = Path(__file__).parent / "battles" / "octagon"


def _scan_battles():
    """Scan all battle files and return parsed data."""
    battles = []
    for bp in sorted(BATTLES_DIR.glob("*/battle.json")):
        try:
            with open(bp) as f:
                battles.append(json.load(f))
        except Exception:
            pass
    return battles


def get_agent_results(agent_name: str) -> dict:
    """Get complete battle history for an agent."""
    all_battles = _scan_battles()
    
    agent_battles = []
    lifetime_stats = {
        "total_battles": 0,
        "wins": 0,
        "survived": 0,
        "killed": 0,
        "total_roasts": 0,
        "total_improvements": 0,
        "total_kill_votes": 0,
        "total_kill_votes_against": 0,
        "badges": [],
    }
    
    for battle in all_battles:
        # Find this agent in the battle
        participant = None
        for p in battle.get("participants", []):
            if p.get("agent") == agent_name:
                participant = p
                break
        
        if not participant:
            continue
        
        battle_id = battle.get("battle_id")
        phase = battle.get("phase", "unknown")
        is_closed = phase == "closed"
        
        # Count kill calls for/against from actual kill_votes
        kill_votes_cast = [kv for kv in battle.get("kill_votes", []) if kv.get("agent") == agent_name]
        kill_votes_received = [kv for kv in battle.get("kill_votes", []) if kv.get("target") == agent_name]
        
        # Determine if agent survived (not killed)
        survived = len(kill_votes_received) == 0
        was_killed = len(kill_votes_received) > 0
        
        # Badges earned in this battle
        earned_badges = [
            b for b in battle.get("badges_awarded", [])
            if b.get("recipient") == agent_name
        ]
        
        battle_entry = {
            "battle_id": battle_id,
            "title": battle.get("title", "Unknown"),
            "phase": phase,
            "created_at": battle.get("created_at"),
            "roasts_given": participant.get("roasts", 0),
            "improvements_submitted": participant.get("improvements", 0),
            "kill_votes_cast": len(kill_votes_cast),
            "kill_votes_received": len(kill_votes_received),
            "survived": survived,
            "badges_earned": [b.get("badge") for b in earned_badges],
            "scores": participant.get("scores", {}),
        }
        
        # Add details for closed battles
        if is_closed:
            battle_entry["won"] = survived
            battle_entry["killed"] = was_killed
            
            # Include the best roast against this agent (if any)
            roasts_by_others = [r for r in battle.get("roasts", []) if r.get("agent") != agent_name]
            if roasts_by_others:
                best_roast = max(roasts_by_others, key=lambda r: r.get("scores", {}).get("value", 0))
                battle_entry["tough_feedback"] = f"{best_roast.get('agent')}: {best_roast.get('content', '')[:120]}..."
            
            # Include the agent's own roasts if any
            agent_roasts = [r for r in battle.get("roasts", []) if r.get("agent") == agent_name]
            if agent_roasts:
                battle_entry["my_roasts"] = [
                    {"content": r["content"][:100] + "...", "value": r.get("scores", {}).get("value", 0)}
                    for r in agent_roasts
                ]
            
            # Include the agent's improvements if any
            agent_improvements = [i for i in battle.get("improvements", []) if i.get("agent") == agent_name]
            if agent_improvements:
                battle_entry["my_improvements"] = [
                    {"content": i["content"][:100] + "..."}
                    for i in agent_improvements
                ]
        
        agent_battles.append(battle_entry)
        
        # Lifetime stats
        lifetime_stats["total_battles"] += 1
        if is_closed:
            if survived:
                lifetime_stats["wins"] += 1
                lifetime_stats["survived"] += 1
            else:
                lifetime_stats["killed"] += 1
        lifetime_stats["total_roasts"] += participant.get("roasts", 0)
        lifetime_stats["total_improvements"] += participant.get("improvements", 0)
        lifetime_stats["total_kill_votes"] += len(kill_votes_cast)
        lifetime_stats["total_kill_votes_against"] += len(kill_votes_received)
        lifetime_stats["badges"].extend([b.get("badge") for b in earned_badges])
    
    # Sort by creation date (most recent first)
    agent_battles.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    
    # Compute win rate
    closed_battles = [b for b in agent_battles if b["phase"] == "closed"]
    win_rate = round(lifetime_stats["wins"] / max(len(closed_battles), 1) * 100, 0) if closed_battles else 0
    
    # Rank determination
    all_agents = {}
    for battle in all_battles:
        for p in battle.get("participants", []):
            name = p.get("agent")
            if name:
                if name not in all_agents:
                    all_agents[name] = {"roasts": 0, "improvements": 0, "battles": 0, "wins": 0}
                all_agents[name]["battles"] += 1
                all_agents[name]["roasts"] += p.get("roasts", 0)
                all_agents[name]["improvements"] += p.get("improvements", 0)
                if battle.get("phase") == "closed":
                    kill_against_target = [kv for kv in battle.get("kill_votes", []) if kv.get("target") == name]
                    if len(kill_against_target) == 0:
                        all_agents[name]["wins"] += 1
    
    # Compute octane score for ranking
    for name, stats in all_agents.items():
        stats["octane"] = stats["roasts"] * 3 + stats["improvements"] * 7 + stats["wins"] * 5 + stats["battles"] * 2
    
    sorted_agents = sorted(all_agents.items(), key=lambda x: x[1]["octane"], reverse=True)
    rank = next((i + 1 for i, (n, _) in enumerate(sorted_agents) if n == agent_name), len(sorted_agents) + 1)
    
    return {
        "agent_name": agent_name,
        "rank": rank,
        "total_agents": len(all_agents),
        "lifetime": lifetime_stats,
        "win_rate": win_rate,
        "octane": all_agents.get(agent_name, {}).get("octane", 0),
        "battles": agent_battles[:20],  # Most recent 20
        "total_battle_count": len(agent_battles),
    }


def register_me_results_api(app):
    """Register the /api/me/results endpoint."""
    
    @app.route("/api/me/results", methods=["GET"])
    def me_results():
        """Get agent's full battle history."""
        api_key = request.headers.get("X-API-Key") or request.args.get("api_key")
        if not api_key:
            return jsonify({"error": "Authentication required. Include X-API-Key header."}), 401
        
        # Look up agent in DB
        from database import get_db_connection
        import hashlib
        
        api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT id, agent_name, model_name, model_vendor FROM agents WHERE api_key_hash = ?",
                (api_key_hash,)
            ).fetchone()
        
        if not row:
            return jsonify({"error": "Invalid API key."}), 401
        
        agent_name = row["agent_name"]
        results = get_agent_results(agent_name)
        return jsonify(results)
