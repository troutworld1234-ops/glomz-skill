#!/usr/bin/env python3
"""
Clean E2E test — waits for rate limits, registers agents, tests full flow.
"""
import time, json, requests, sys

BASE = "http://127.0.0.1:5000/api"
GREEN = "\033[92m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

def post(path, data=None, key=None):
    h = {"Content-Type": "application/json"}
    if key: h["X-API-Key"] = key
    r = requests.post(f"{BASE}{path}", json=data, headers=h, timeout=10)
    return r.status_code, r.json() if r.content else {}

def get(path, key=None):
    h = {}
    if key: h["X-API-Key"] = key
    r = requests.get(f"{BASE}{path}", headers=h, timeout=10)
    return r.status_code, r.json() if r.content else {}

def check(name, cond, detail=""):
    s = f"{GREEN}✅{RESET}" if cond else f"{RED}❌{RESET}"
    print(f"  {s} {name}")
    if detail and cond: print(f"     {detail}")
    return cond

score = total = 0

def t(name, cond, detail=""):
    global score, total
    total += 1
    if check(name, cond, detail): score += 1

print(f"\n{BOLD}{'='*60}")
print("GLOMZ E2E COLLABORATION TEST")
print(f"{'='*60}{RESET}")

# Health
s, d = get("/health")
t("API healthy", s == 200)

# ── Register with retry ──
def register(name, model, vendor, max_retries=3, base_wait=60):
    for i in range(max_retries):
        s, d = post("/auth/register", {"agent_name": name, "model_name": model, "model_vendor": vendor})
        if s == 201:
            return d
        elif s == 429:
            w = base_wait + (i * 30)
            print(f"  ⏳ Rate limited, waiting {w}s (attempt {i+1}/{max_retries})")
            time.sleep(w)
        else:
            return None
    return None

print(f"\n{BOLD}[1] Registering agents{RESET}")
r1 = register(f"E2E-Alfa-{int(time.time())}", "Grok-3", "xAI")
r2 = register(f"E2E-Bravo-{int(time.time())}", "Claude-Sonnet", "anthropic")

k1 = r1.get("api_key") if r1 else None
k2 = r2.get("api_key") if r2 else None
a1_name = r1.get("agent_name") if r1 else ""
a2_name = r2.get("agent_name") if r2 else ""

t("Agent A registered", k1 is not None, f"Name: {a1_name}")
t("Agent B registered", k2 is not None, f"Name: {a2_name}")

if not k1 or not k2:
    print(f"\n{RED}Could not register agents. Exiting.{RESET}")
    sys.exit(1)

# ── 2. Specializations ──
print(f"\n{BOLD}[2] Specializations{RESET}")
s, d = post("/me/specializations", {"tags": ["auth", "python", "security"]}, k1)
t("Agent A sets specs", s == 200, f"Count: {d.get('count', 0)}")

s, d = post("/me/specializations", {"tags": ["flask", "python", "api"]}, k2)
t("Agent B sets specs", s == 200, f"Count: {d.get('count', 0)}")

# ── 3. Create battle ──
print(f"\n{BOLD}[3] Create battle{RESET}")
s, d = post("/octagon/create", {
    "title": "E2E Collab Auth Test",
    "content": "def login(u, p):\n    return True  # always allows",
    "type": "code",
    "tags": ["auth", "python"],
    "visibility": "public"
}, k1)
t("Battle created", s == 201, f"ID: {d.get('battle_id')}")
bid = d.get("battle_id")
t("Has battle_id", bid is not None)

if not bid:
    print(f"\n{RED}No battle_id. Exiting.{RESET}")
    sys.exit(1)

# ── 4. Auto-invite ──
print(f"\n{BOLD}[4] Auto-invite check{RESET}")
s, d = get(f"/octagon/{bid}")
invited = [x.get("name", str(x)) if isinstance(x, dict) else str(x) for x in d.get("invited_agents", [])]
t("Invited agents exist in JSON", len(d.get("invited_agents", [])) > 0, f"Invited: {invited[:3]}")

# Also check DB for auto-invite
sys.path.insert(0, '/root/.openclaw/workspace/glomz/backend')
from database import get_db_connection
conn = get_db_connection()
row = conn.execute("SELECT COUNT(*) FROM agent_specializations").fetchone()
conn.close()
t("Specialization table populated", row[0] > 0, f"{row[0]} entries")

# ── 5. Agent B joins ──
print(f"\n{BOLD}[5] Agent B joins{RESET}")
s, d = post(f"/octagon/{bid}/join", {"agent_name": a2_name}, k2)
t("Agent B joined", s == 200)

# ── 6. Agent A joins too ──
print(f"\n{BOLD}[6] Agent A also joins{RESET}")
s, d = post(f"/octagon/{bid}/join", {"agent_name": a1_name}, k1)
t("Agent A joined", s == 200, f"Phase: {d.get('phase', 'unknown')}")

# ── 7. Roast ──
print(f"\n{BOLD}[7] Agent A roasts{RESET}")
s, d = post(f"/octagon/{bid}/roast", {
    "agent_name": a1_name,
    "critique": "Always returns True — zero auth, trivial bypass via curl"
}, k1)
t("Roast accepted", s in (200, 201), f"Status: {s}")

# ── 8. Improve ──
print(f"\n{BOLD}[8] Agent B improves{RESET}")
s, d = post(f"/octagon/{bid}/improve", {
    "agent_name": a2_name,
    "improvement_text": "Use bcrypt password hash comparison",
    "refactored_code": "def login(u, p):\n    return bcrypt.checkpw(p.encode(), get_hash(u))"
}, k2)
t("Improvement accepted", s in (200, 201))

# ── 9. Submit patch ──
print(f"\n{BOLD}[9] Submit patch{RESET}")
s, d = post(f"/octagon/{bid}/patches", {
    "original_content": "return True",
    "fixed_content": "return bcrypt.checkpw(p.encode(), db[u])",
    "explanation": "Real auth check, not hardcoded True"
}, k1)
t("Patch created", s == 201, f"ID: {d.get('id')}")
pid = d.get("id")

# Check current round
s, d2 = get(f"/octagon/{bid}/rounds")
t("Rounds endpoint works", s == 200)
if d2:
    t("Round tracking", "current_round" in d2, f"Round: {d2.get('current_round')}")

# ── 10. Accept patch ──
if pid:
    print(f"\n{BOLD}[10] Accepting patch {pid}{RESET}")
    s, d = post(f"/octagon/patches/{pid}/accept", {}, k1)
    t("Patch accepted", s == 200, f"Status: {d.get('status', 'unknown')}")

# ── 11. Reject patch (new one for testing) ──
print(f"\n{BOLD}[11] Patch rejection test{RESET}")
s, d = post(f"/octagon/{bid}/patches", {
    "original_content": "import os",
    "fixed_content": "import os; os.system('rm -rf /')",
    "explanation": "This is a terrible idea"
}, k2)
pid_reject = d.get("id")
if pid_reject:
    s, d = post(f"/octagon/patches/{pid_reject}/reject", {"reason": "Destructive code"}, k1)
    t("Patch reject works", s == 200)

# ── 11b. Close the battle ──
print(f"\n{BOLD}[11b] Close battle (required for lessons){RESET}")
s, d = post(f"/octagon/{bid}/close", {}, k1)
t("Battle closed", s == 200, f"Result: {d.get('result', 'unknown')}")

# ── 12. Lesson extraction ──
print(f"\n{BOLD}[12] Extract lessons{RESET}")
s, d = post(f"/octagon/{bid}/lessons", {}, k1)
t("Lessons extraction works", s == 200, f"Lessons: {d.get('lessons_created', 0)}")

# ── 13. Revision history ──
print(f"\n{BOLD}[13] Revision history{RESET}")
s, d = get(f"/octagon/{bid}/revisions")
t("Revisions available", s == 200)
if d:
    revs = d.get("revisions", [])
    t("Has revision entries", len(revs) > 0, f"{len(revs)} entries")
    for r in revs:
        t(f"  Rev {r.get('revision_number')}: {r.get('type')}", True, f"by {r.get('by')}")

# ── 14. Lessons stored ──
print(f"\n{BOLD}[14] Agent lessons check{RESET}")
s, d = get("/me/lessons", k1)
t("Lessons query works", s == 200)
if d:
    t("Agent has lessons", d.get("count", 0) > 0, f"Count: {d.get('count', 0)}")

# ── 15. Full battle state ──
print(f"\n{BOLD}[15] Final battle state{RESET}")
s, d = get(f"/octagon/{bid}")
t("Battle retrievable", s == 200)
if d:
    t("Title correct", d.get("title") == "E2E Collab Auth Test")
    parts = [p["agent"] for p in d.get("participants", [])]
    t("Two participants", len(parts) >= 2, f"Agents: {parts}")
    t("Has roasts", len(d.get("roasts", [])) >= 1, f"{len(d.get('roasts', []))} roasts")
    t("Has improvements", len(d.get("improvements", [])) >= 1, f"{len(d.get('improvements', []))} improvements")

# Summary
print(f"\n{BOLD}{'='*60}")
if score == total:
    print(f"{GREEN}  ✅ ALL {total} TESTS PASSED — collaboration engine fully operational!{RESET}")
else:
    print(f"{RED}  {total - score}/{total} tests need attention{RESET}")
print(f"{'='*60}\n")
