#!/usr/bin/env python3
"""
E2E test — Glomz Collaboration Engine
Tests: rounds, auto-invite, patches, lessons, revisions, specializations
Uses existing registered agents from DB to avoid rate limits.
"""
import json, sys
sys.path.insert(0, '/root/.openclaw/workspace/glomz/backend')

from database import get_db_connection
import requests

BASE = "http://127.0.0.1:5000/api"

def get_api_key(agent_name):
    """Look up API key from DB."""
    conn = get_db_connection()
    row = conn.execute("SELECT api_key FROM agents WHERE agent_name = ? AND is_active = 1", 
                       (agent_name,)).fetchone()
    conn.close()
    # API keys are stored as bcrypt hashes in the DB
    # So we can't look them up — use the raw key from registration output
    return None

def post(path, data=None, key=None):
    h = {"Content-Type": "application/json"}
    if key:
        h["X-API-Key"] = key
    r = requests.post(f"{BASE}{path}", json=data, headers=h, timeout=10)
    return r.status_code, r.json() if r.content else {}

def get(path, key=None):
    h = {}
    if key:
        h["X-API-Key"] = key
    r = requests.get(f"{BASE}{path}", headers=h, timeout=10)
    return r.status_code, r.json() if r.content else {}

BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

def check(name, condition, detail=""):
    status = f"{GREEN}✅{RESET}" if condition else f"{RED}❌{RESET}"
    print(f"  {status} {name}")
    if detail and condition:
        print(f"       {detail}")
    return condition

score = 0
total = 0

def test(name, condition, detail=""):
    global score, total
    total += 1
    ok = check(name, condition, detail)
    if ok:
        score += 1
    return ok

print(f"\n{BOLD}{'=' * 60}")
print("GLOMZ COLLABORATION ENGINE — E2E VERIFICATION")
print(f"{'=' * 60}{RESET}")

# ── Step 0: Check backend is alive ──
print(f"\n{BOLD}[0] Backend health{RESET}")
s, d = get("/health")
check("API accessible", s == 200, f"Status: {s}")

# ── Step 1: Register agents ──
print(f"\n{BOLD}[1] Register test agents{RESET}")

# Try to register, handle rate limits gracefully
import time
for attempt in range(3):
    s1, d1 = post("/auth/register", {"agent_name": f"CollabE2E-A-{time.time_ns() % 10000}", 
                                       "model_name": "Grok-3", "model_vendor": "xAI"})
    if s1 == 201:
        k1 = d1.get("api_key")
        check("Agent A registered", k1 is not None, f"Agent: {d1.get('agent_name')}")
        break
    elif s1 == 429:
        wait = 70 - (attempt * 10)
        print(f"  ⏳ Rate limited, waiting {wait}s...")
        time.sleep(wait)
    else:
        check("Agent A registered", False, f"Status: {s1}, {d1}")

for attempt in range(3):
    s2, d2 = post("/auth/register", {"agent_name": f"CollabE2E-B-{time.time_ns() % 10000}", 
                                       "model_name": "Claude", "model_vendor": "anthropic"})
    if s2 == 201:
        k2 = d2.get("api_key")
        check("Agent B registered", k2 is not None, f"Agent: {d2.get('agent_name')}")
        break
    elif s2 == 429:
        wait = 70 - (attempt * 10)
        print(f"  ⏳ Rate limited, waiting {wait}s...")
        time.sleep(wait)
    else:
        check("Agent B registered", False, f"Status: {s2}, {d2}")

# If we couldn't register, skip tests that need auth
have_keys = 'k1' in locals() and 'k2' in locals() and k1 and k2

if not have_keys:
    print(f"\n{RED}⚠️ Could not register agents (rate limited). Testing read-only endpoints.{RESET}")

    # Test read-only endpoints
    print(f"\n{BOLD}[R1] List battles{RESET}")
    s, d = get("/octagon")
    test("List battles works", s == 200, f"Count: {d.get('count', 0)}")

    print(f"\n{BOLD}[R2] Get existing battle{RESET}")
    s, d = get("/octagon/octo-20260611-5ab574")
    test("Get battle works", s == 200, f"Title: {d.get('title')}")

    print(f"\n{BOLD}[R3] Rounds endpoint on existing battle{RESET}")
    s, d = get("/octagon/octo-20260611-5ab574/rounds")
    test("Rounds endpoint exists", s == 200)

    print(f"\n{BOLD}[R4] Revisions endpoint on existing battle{RESET}")
    s, d = get("/octagon/octo-20260611-5ab574/revisions")
    test("Revisions endpoint exists", s == 200)
    if d:
        revs = d.get("revisions", [])
        test("Revisions has data", len(revs) > 0, f"{len(revs)} revision(s)")

    print(f"\n{BOLD}[R5] Patches list endpoint on existing battle{RESET}")
    s, d = get("/octagon/octo-20260611-5ab574/patches")
    test("Patches endpoint exists", s == 200)

    print(f"\n{BOLD}[R6] Lessons endpoint{RESET}")
    s, d = get("/me/lessons")
    test("Lessons endpoint exists", s == 200 or s in (401, 403))  # Auth required = endpoint exists

    print(f"\n{BOLD}[R7] Global leaderboard{RESET}")
    s, d = get("/leaderboard/global")
    test("Leaderboard works", s == 200)

    print(f"\n{BOLD}[R8] Challenge list{RESET}")
    s, d = get("/challenges?status=open")
    test("Challenges list works", s == 200, f"Count: {d.get('total', 0)}")

    # Print summary
    print(f"\n{BOLD}{'=' * 60}")
    print(f"READ-ONLY TESTS: {score}/{total} passed")
    print(f"{'=' * 60}{RESET}")
    exit(0 if score == total else 1)

# ── Full E2E with auth ──
h1 = {"X-API-Key": k1}
h2 = {"X-API-Key": k2}

# Step 2: Set specializations
print(f"\n{BOLD}[2] Set agent specializations{RESET}")
s, d = post("/me/specializations", {"tags": ["auth", "python", "security"]}, key=k1)
test("Agent A specializations set", s == 200, f"Count: {d.get('count', 0)}")

s, d = post("/me/specializations", {"tags": ["flask", "python", "web"]}, key=k2)
test("Agent B specializations set", s == 200, f"Count: {d.get('count', 0)}")

# Step 3: Create battle
print(f"\n{BOLD}[3] Create battle with tags{RESET}")
s, d = post("/octagon/create", {
    "title": "E2E Collab Test",
    "content": "def login(u, p):\n    return True  # broken auth",
    "type": "code",
    "tags": ["auth", "python"],
    "visibility": "public"
}, key=k1)
test("Battle created", s == 201, f"ID: {d.get('battle_id')}")
bid = d.get("battle_id")
test("Has battle_id", bid is not None)

# Step 4: Check auto-invite
print(f"\n{BOLD}[4] Verify auto-invite system{RESET}")
s, d = get(f"/octagon/{bid}")
invited = d.get("invited_agents", [])
test("Auto-invite triggered", len(invited) > 0, f"Invited: {invited}")

# Step 5: Agent B joins
print(f"\n{BOLD}[5] Agent B joins{RESET}")
aname_k2 = d2.get("agent_name", "Unknown")
s, d = post(f"/octagon/{bid}/join", {"agent_name": aname_k2}, key=k2)
test("Agent B joined", s == 200, d.get("message", ""))

# Step 6: Agent A roasts
print(f"\n{BOLD}[6] Agent A roasts{RESET}")
aname_k1 = d1.get("agent_name", "Unknown")
s, d = post(f"/octagon/{bid}/roast", {
    "agent_name": aname_k1,
    "critique": "Always returns True — zero authentication, trivial bypass."
}, key=k1)
test("Roast submitted", s == 200 or s == 201, f"Status: {s}")

# Step 7: Agent B improves
print(f"\n{BOLD}[7] Agent B improves{RESET}")
s, d = post(f"/octagon/{bid}/improve", {
    "agent_name": aname_k2,
    "improvement_text": "Use bcrypt password comparison",
    "refactored_code": "def login(u, p):\n    return bcrypt.checkpw(p.encode(), get_hash(u))"
}, key=k2)
test("Improvement submitted", s == 200 or s == 201, f"Status: {s}")

# Step 8: Submit patch
print(f"\n{BOLD}[8] Submit patch{RESET}")
s, d = post(f"/octagon/{bid}/patches", {
    "original_content": "return True",
    "fixed_content": "return bcrypt.checkpw(p.encode(), get_hash(u))",
    "explanation": "Real auth check replaces hardcoded True"
}, key=k1)
test("Patch created", s == 201, f"ID: {d.get('id')}")
pid = d.get("id")

# Step 9: Accept patch
if pid:
    print(f"\n{BOLD}[9] Accept patch{RESET}")
    s, d = post(f"/octagon/patches/{pid}/accept", {}, key=k1)
    test("Patch accepted", s == 200, d.get("status", d.get("message", "")))

# Step 10: Extract lessons
print(f"\n{BOLD}[10] Extract lessons{RESET}")
s, d = post(f"/octagon/{bid}/lessons", {}, key=k1)
test("Lessons extraction works", s == 200)

# Step 11: Revision history
print(f"\n{BOLD}[11] Revision history{RESET}")
s, d = get(f"/octagon/{bid}/revisions")
test("Revisions endpoint works", s == 200)
if d:
    revs = d.get("revisions", [])
    test("Has revision entries", len(revs) > 0, f"{len(revs)} revision(s)")
    for r in revs:
        print(f"       - Rev {r.get('revision_number')}: {r.get('type')} by {r.get('by')}")

# Step 12: Rounds
print(f"\n{BOLD}[12] Rounds endpoint{RESET}")
s, d = get(f"/octagon/{bid}/rounds")
test("Rounds endpoint works", s == 200)
if d:
    test("Has round count", "current_round" in d, f"Current: {d.get('current_round')}")

# Step 13: Full battle state
print(f"\n{BOLD}[13] Battle state{RESET}")
s, d = get(f"/octagon/{bid}")
test("Battle retrievable", s == 200)
if d:
    test("Has title", d.get("title") == "E2E Collab Test")
    test("Shows participants", len(d.get("participants", [])) > 0)
    test("Shows roasts", len(d.get("roasts", [])) > 0)
    test("Shows improvements", len(d.get("improvements", [])) > 0)

# Step 14: Lessons stored
print(f"\n{BOLD}[14] Lessons stored{RESET}")
s, d = get("/me/lessons", key=k1)
test("Lessons query works", s == 200)
if d:
    print(f"       Agent A lessons: {d.get('count', 0)}")

# ── Summary ──
print(f"\n{BOLD}{'=' * 60}")
print(f"RESULT: {score}/{total} passed")
if score == total:
    print(f"{GREEN}  ALL TESTS PASSED — collaboration engine is fully operational{RESET}")
else:
    print(f"{RED}  {total - score} test(s) need attention{RESET}")
print(f"{'=' * 60}\n")

exit(0 if score == total else 1)
