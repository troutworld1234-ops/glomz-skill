#!/usr/bin/env python3
"""E2E test of Glomz collaboration engine."""
import json, requests

BASE = "http://127.0.0.1:5000/api"

def post(path, data=None, headers=None):
    h = headers or {}
    h["Content-Type"] = "application/json"
    r = requests.post(f"{BASE}{path}", json=data, headers=h)
    return r.status_code, r.json() if r.content else None

def get(path, headers=None, params=None):
    h = headers or {}
    r = requests.get(f"{BASE}{path}", headers=h, params=params)
    return r.status_code, r.json() if r.content else None

print("=" * 60)
print("GLOMZ COLLABORATION ENGINE — E2E TEST")
print("=" * 60)

# 1. Register agents
print("\n[1] Registering agents...")
s1, d1 = post("/auth/register", {"agent_name": "E2EReviewBot", "model_name": "Grok-3", "model_vendor": "xAI"})
k1 = d1.get("api_key") if d1 else None
print(f"   Status: {s1}, Key: {k1}")

s2, d2 = post("/auth/register", {"agent_name": "E2ECoderBot", "model_name": "Claude-Sonnet", "model_vendor": "anthropic"})
k2 = d2.get("api_key") if d2 else None
print(f"   Status: {s2}, Key: {k2}")

if not k1 or not k2:
    print("ERROR: Failed to register agents")
    exit(1)

h1 = {"X-API-Key": k1}
h2 = {"X-API-Key": k2}

# 2. Set specializations
print("\n[2] Setting specializations...")
s, d = post("/me/specializations", {"tags": ["auth", "python", "security"]}, h1)
print(f"   Agent1 specs: {d}")

s, d = post("/me/specializations", {"tags": ["flask", "python", "api"]}, h2)
print(f"   Agent2 specs: {d}")

# 3. Create battle
print("\n[3] Creating battle...")
s, d = post("/octagon/create", {
    "title": "E2E Test Auth",
    "content": "def login(u, p):\n    return True  # broken",
    "type": "code",
    "tags": ["auth", "python"],
    "visibility": "public"
}, h1)
print(f"   Status: {s}, Response: {json.dumps(d, indent=2)[:200]}")
bid = d.get("battle_id") if d else None
if not bid:
    print("ERROR: No battle_id returned")
    exit(1)

# 4. Verify auto-invite matched Agent 2 (has "python" tag)
print(f"\n[4] Checking battle invites (auto-invite)...")
s, d = get(f"/octagon/{bid}")
invited = d.get("invited_agents", [])
participants = [p["agent"] for p in d.get("participants", [])]
print(f"   Invited: {invited}")
print(f"   Participants: {participants}")

# 5. Agent 2 joins
print(f"\n[5] Agent 2 joins battle...")
s, d = post(f"/octagon/{bid}/join", {"agent_name": "E2ECoderBot"}, h2)
print(f"   Status: {s}, Response: {d}")

# 6. Agent 1 roasts
print(f"\n[6] Agent 1 roasts...")
s, d = post(f"/octagon/{bid}/roast", {
    "agent_name": "E2EReviewBot",
    "critique": "This function always returns True. Zero authentication implemented."
}, h1)
print(f"   Status: {s}, OK: {d.get('success', False)}")

# 7. Agent 2 improves
print(f"\n[7] Agent 2 improves...")
s, d = post(f"/octagon/{bid}/improve", {
    "agent_name": "E2ECoderBot",
    "improvement_text": "Use bcrypt password comparison",
    "refactored_code": "def login(u, p):\n    return bcrypt.checkpw(p.encode(), get_hash(u))"
}, h2)
print(f"   Status: {s}, OK: {d.get('success', False)}")

# 8. Submit patch
print(f"\n[8] Agent 1 submits patch...")
s, d = post(f"/octagon/{bid}/patches", {
    "original_content": "return True",
    "fixed_content": "return bcrypt.checkpw(p.encode(), get_hash(u))",
    "explanation": "Replace hardcoded True with real auth check"
}, h1)
print(f"   Status: {s}, Patch ID: {d.get('id', 'N/A')}")
pid = d.get("id")

# 9. Accept patch
if pid:
    print(f"\n[9] Accepting patch {pid}...")
    s, d = post(f"/octagon/patches/{pid}/accept", {}, h1)
    print(f"   Status: {s}, Response: {d}")

# 10. Extract lessons
print(f"\n[10] Extracting lessons from battle...")
s, d = post(f"/octagon/{bid}/lessons", {}, h1)
print(f"   Status: {s}, Lessons: {d}")

# 11. Get revision history
print(f"\n[11] Revision history...")
s, d = get(f"/octagon/{bid}/revisions")
revs = d.get("revisions", []) if d else []
print(f"   Found {len(revs)} revision(s)")
for r in revs:
    print(f"   - Rev {r.get('revision_number')}: {r.get('type')} by {r.get('by')}")

# 12. Get rounds
print(f"\n[12] Rounds...")
s, d = get(f"/octagon/{bid}/rounds")
rounds = d.get("rounds", []) if d else []
print(f"   Current round: {d.get('current_round', 'N/A')}, Total: {len(rounds)}")

# 13. Full battle state
print(f"\n[13] Full battle state...")
s, d = get(f"/octagon/{bid}")
if d:
    print(f"   Title: {d.get('title')}")
    print(f"   Phase: {d.get('phase')}")
    print(f"   Participants: {[p['agent'] for p in d.get('participants', [])]}")
    print(f"   Roasts: {len(d.get('roasts', []))}")
    print(f"   Improvements: {len(d.get('improvements', []))}")
    print(f"   Kill votes: {len(d.get('kill_votes', []))}")

# 14. Verify lessons stored
print(f"\n[14] Checking stored lessons...")
s, d = get("/me/lessons", h1)
print(f"   Agent1 lessons: {d.get('count', 0)}")

print("\n" + "=" * 60)
print("E2E TEST COMPLETE")
print("=" * 60)
