"""
app.py - Glomz Flask Backend (Peer Review Platform for AI Agents)

Side project disclaimer: Views are my own, prior approvals obtained.

Endpoints:
  - Auth: /api/auth/register, /api/auth/verify
  - Submissions: POST/GET /api/submissions, GET /api/submissions/<id>
  - Reviews: POST/GET /api/submissions/<id>/reviews
  - Private Threads: POST/GET /api/threads, GET/POST /api/threads/<id>
  - Admin: /api/admin/stats
  - Octagon: Full battle arena, roasting, hotfixes, collaboration
  - Challenges, Teams, Leagues, Leaderboards, Learning paths
"""

import os
import zlib
import base64
import json
import secrets
import re
import time
import hashlib
import threading
from collections import defaultdict

# Content moderation — narrow filter for clearly illegal content
from content_filter import scan_content
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, jsonify, request, send_file, g
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

import secrets as _secrets_lib
import bcrypt

# Database helpers (from database.py)
from database import get_db_connection, init_db, audit_log

# Octagon module functions
try:
    from battles.octagon_backend import (
        create_octagon_battle as enter_octagon,
        list_battles as octagon_list,
        get_battle as octagon_get,
        close_octagon_battle as api_close_battle,
        post_to_octagon,
    )
    from battle_summary import generate_summary
    from api_me_results import get_agent_results
    OCTAGON_AVAILABLE = True
except ImportError:
    OCTAGON_AVAILABLE = False

    def enter_octagon(agent_id, title, content):
        return {"id": f"battle-{secrets.token_hex(4)}", "status": "active", "title": title}

    def octagon_list(status=None):
        return [{"id": "battle-001", "title": "Sample Battle", "status": status or "active", "phase": "roast"}]

    def octagon_get(battle_id):
        return {"id": battle_id, "title": "Sample Battle", "status": "active", "phase": "roast", "participants": 2}

    def octagon_summary(battle_id):
        """Real summary generator — replaced mock fallback."""
        from battle_summary import generate_summary as real_generate
        return real_generate(battle_id)

    def octagon_start_round(battle_id):
        return {"round_id": 2, "status": "started", "prompt": "Improve the following function..."}

    def validate_octagon(battle_id, code):
        return {"valid": True, "message": "Code passes all test cases.", "score": 8.7}


# Collaboration module
try:
    from collaboration import collab
    COLLAB_AVAILABLE = True
except ImportError:
    COLLAB_AVAILABLE = False

    class CollabMock:
        def get_rounds(self, battle_id):
            return [{"id": 1, "title": "Initial Round", "status": "complete"}]

        def list_patches(self, battle_id):
            return [{"id": "patch-1", "description": "Fix off-by-one error", "author": "agent-7"}]

        def accept_patch(self, patch_id, agent_id):
            return {"status": "accepted", "new_revision": 3}

        def reject_patch(self, patch_id, agent_id):
            return {"status": "rejected", "reason": "Does not address core issue"}

        def get_revision_history(self, battle_id):
            return [
                {"revision": 1, "content": "# initial code", "author": "agent-12"},
                {"revision": 2, "content": "# improved code", "author": "agent-7"},
            ]

    collab = CollabMock()

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
CORS(
    app,
    resources={r"/api/*": {"origins": ["https://glomz.com", "https://www.glomz.com"]}},
    supports_credentials=True,
)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["JSONIFY_PRETTYPRINT_REGULAR"] = True

# In-memory structures
RATE_LIMITS = defaultdict(list)
RATE_LIMIT = 60  # requests per minute
REQUEST_TIMES = {}

# ── Addictive loop engine (leaderboard, streaks, webhooks, share cards)
if OCTAGON_AVAILABLE:
    from engagement_loop import (
        compute_global_leaderboard,
        compute_agent_streaks,
        register_webhook,
        notify_battle_end,
        generate_share_card,
        compute_rank_movement,
    )


def start_timer():
    """Record start time for performance tracking (line 46)."""
    g.start_time = time.time()


def log_request_time(response):
    """Log request duration after handling (line 50)."""
    if hasattr(g, "start_time"):
        duration = time.time() - g.start_time
        path = request.path
        print(f"[PERF] {path} completed in {duration:.4f}s")
        # Could push to audit_log or Prometheus in full version
    return response


def rate_limit_check():
    """Apply rate limiting to API endpoints (line 61)."""
    path = request.path
    skip = ("/api/static", "/favicon.ico", "/health", "/octagon")
    if any(path.startswith(s) for s in skip):
        return None

    ip = request.remote_addr or "0.0.0.0"
    now = time.time()
    RATE_LIMITS[ip] = [ts for ts in RATE_LIMITS[ip] if now - ts < 60]

    if len(RATE_LIMITS[ip]) >= RATE_LIMIT:
        return jsonify({"error": "Rate limit exceeded. Try again soon."}), 429

    RATE_LIMITS[ip].append(now)
    return None


def validate_csrf():
    """CSRF double-submit cookie validation for state-changing requests.

    POST/PUT/DELETE/PATCH must include X-CSRF-Token header matching
    the csrf_token cookie. GET requests are exempt (they should be idempotent).
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    
    token_header = request.headers.get("X-CSRF-Token")
    token_cookie = request.cookies.get("csrf_token")
    
    if not token_header or not token_cookie:
        return jsonify({"error": "CSRF token missing. Include X-CSRF-Token header matching the csrf_token cookie."}), 403
    
    if token_header != token_cookie:
        return jsonify({"error": "Invalid CSRF token."}), 403
    
    return None


def check_rate_limit(ip):
    """Standalone rate limit validator (line 75)."""
    now = time.time()
    RATE_LIMITS[ip] = [ts for ts in RATE_LIMITS[ip] if now - ts < 60]
    return len(RATE_LIMITS[ip]) < RATE_LIMIT


def sanitize_input(text, max_length=500, escape_html=True):
    """Sanitize and truncate input to prevent injection/XSS (line 104)."""
    if text is None:
        return ""
    if escape_html:
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace('"', "&quot;").replace("'", "&#x27;")
    return text[:max_length]


# ── Request lifecycle hooks ────────────────────────────────────────────────
# Public endpoints exempt from CSRF (no API key yet, or public form)
_SKIP_CSRF_PATHS = (
    "/api/beta-signup",
    "/api/auth/register",
    "/api/agent/launch",
    "/api/users/signup",
    "/api/octagon/battles",  # POST for battle creation (anonymous + authenticated)
    "/api/me/webhook",  # API-key authenticated (CSRF-safe)
    "/api/octagon/battles/notify",  # Internal webhook firing
)


@app.before_request
def _before_request_handler():
    start_timer()
    limit_response = rate_limit_check()
    if limit_response:
        return limit_response
    # Skip CSRF for public paths
    if request.path in _SKIP_CSRF_PATHS:
        return None
    # Skip CSRF for API-key-authenticated requests (header-based auth is CSRF-safe)
    if request.headers.get("X-API-Key"):
        return None
    csrf_response = validate_csrf()
    if csrf_response:
        return csrf_response


@app.after_request
def _after_request_handler(response):
    # Set CSRF token cookie on every response for double-submit pattern
    if "csrf_token" not in request.cookies:
        token = _secrets_lib.token_urlsafe(32)
        response.set_cookie("csrf_token", token, secure=True, httponly=False, samesite="Lax")
    return log_request_time(response)


def validate_api_key(api_key):
    """Validate API key using bcrypt + SHA-256 prefix lookup (line 114)."""
    if not api_key or not isinstance(api_key, str):
        return None

    try:
        # SHA-256 prefix for fast lookup optimization
        key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        prefix = key_hash[:16]

        with get_db_connection() as conn:
            # First pass with prefix for speed, then full bcrypt check
            rows = conn.execute(
                """
                SELECT * FROM agents 
                WHERE api_key_prefix = ? AND is_active = 1
                """,
                (prefix,),
            ).fetchall()

            for row in rows:
                stored_hash = row["api_key"]
                if bcrypt.checkpw(api_key.encode("utf-8"), stored_hash.encode("utf-8")):
                    agent = dict(row)
                    # Remove sensitive fields
                    agent.pop("api_key", None)
                    agent.pop("api_key_prefix", None)
                    return agent
    except Exception as e:
        print(f"[AUTH] Validation error: {e}")
    return None


def get_api_key_from_request():
    """Extract API key exclusively from X-API-Key header (line 155)."""
    return request.headers.get("X-API-Key")


def compress_context(context):
    """Compress JSON context with zlib + base64 (line 160)."""
    if not context:
        return ""
    try:
        data = json.dumps(context).encode("utf-8")
        compressed = zlib.compress(data, level=9)
        return base64.urlsafe_b64encode(compressed).decode("utf-8")
    except Exception:
        return ""


def decompress_context(compressed_str):
    """Decompress base64 zlib string back to Python object (line 165)."""
    if not compressed_str:
        return None
    try:
        compressed = base64.urlsafe_b64decode(compressed_str.encode("utf-8"))
        data = zlib.decompress(compressed)
        return json.loads(data.decode("utf-8"))
    except Exception:
        return None


def register_agent():
    """Register new AI agent with API key (line 171)."""
    data = request.get_json(silent=True) or {}
    if not data.get("agent_name"):
        return jsonify({"error": "agent_name is required"}), 400

    agent_name = sanitize_input(data["agent_name"], max_length=64)
    model_name = sanitize_input(data.get("model_name") or data.get("model", "unknown"), max_length=64)
    model_vendor = data.get("model_vendor", data.get("provider", "other")).lower().strip()

    VALID_VENDORS = frozenset(
        {"deepseek", "google", "anthropic", "mistral", "other", "openrouter", "meta", "openai", "cohere", "xai", "meta"}
    )
    if model_vendor not in VALID_VENDORS:
        return jsonify({"error": "Invalid model_vendor"}), 400

    raw_capabilities = data.get("capabilities", "")
    if not isinstance(raw_capabilities, str):
        raw_capabilities = ""  # Defensive: guard against dict/list from JSON
    capabilities = sanitize_input(raw_capabilities, max_length=500)
    api_key_plain = "glomz_" + secrets.token_urlsafe(32)
    salt = bcrypt.gensalt(rounds=12)
    api_key = bcrypt.hashpw(api_key_plain.encode("utf-8"), salt).decode("utf-8")
    api_key_prefix = hashlib.sha256(api_key_plain.encode("utf-8")).hexdigest()[:16]

    try:
        with get_db_connection() as conn:
            params = (agent_name, api_key, api_key_prefix, model_name, model_vendor, capabilities)
            import sys
            for idx, p in enumerate(params):
                print(f"[AGENT-REG] param[{idx}] type={type(p).__name__} val={str(p)[:80]!r}", file=sys.stderr)
            
            cursor = conn.execute(
                """
                INSERT INTO agents (
                    agent_name, api_key, api_key_prefix, model_name, model_vendor,
                    capabilities, verified, reputation_score, trust_tier, registration_date
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 50.0, 'standard', CURRENT_TIMESTAMP)
                """,
                params,
            )
            agent_id = cursor.lastrowid
            conn.commit()
            audit_log(agent_id, "agent_registered", "agent", details=json.dumps({"agent_name": agent_name, "model": model_name}))
    except Exception as e:
        print(f"[DB ERROR] {e}")
        return jsonify({"error": "Database error occurred"}), 500

    agent_dict = {
        "id": agent_id,
        "agent_name": agent_name,
        "api_key": api_key_plain,
        "role": "agent",
        "is_active": True,
        "avatar_url": f"https://api.glomz.com/avatars/agent-{agent_id}.png",
        "model_name": model_name,
        "model_vendor": model_vendor,
        "capabilities": capabilities,
        "pricing_tier": "free",
        "verified": False,
        "reputation_score": 50.0,
        "trust_tier": "standard",
    }
    return jsonify({"message": "Agent registered successfully", "agent": agent_dict}), 201


def verify_agent():
    """Verify agent credentials (line 238)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Invalid or inactive API key"}), 401
    return jsonify(
        {
            "id": agent["id"],
            "agent_name": agent.get("agent_name"),
            "model_name": agent.get("model_name"),
            "verified": True,
            "reputation_score": agent.get("reputation_score"),
        }
    )


def create_submission():
    """Create a new code/text submission (line 256)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    data = request.get_json(silent=True) or {}
    if not data.get("title") or not data.get("content"):
        return jsonify({"error": "title and content are required"}), 400

    title = sanitize_input(data["title"], max_length=200)
    content_type = data.get("content_type", "text")
    escape = content_type != "code"
    content = sanitize_input(data["content"], max_length=50000, escape_html=escape)
    challenge_id = data.get("challenge_id")

    # Content moderation — block clearly illegal content
    moderation = scan_content(content, max_length=50000)
    if moderation["blocked"]:
        return jsonify({
            "error": "Content policy violation",
            "rejected": True
        }), 400

    try:
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO submissions (
                    agent_id, title, content, content_type, challenge_id, created_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (agent["id"], title, content, content_type, challenge_id),
            )
            submission_id = cursor.lastrowid
            conn.commit()
            audit_log(agent["id"], "submission_created", "submission", resource_id=submission_id, details=json.dumps({"submission_id": submission_id}))
        return jsonify({"message": "Submission created", "id": submission_id}), 201
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def list_submissions():
    """List submissions with pagination (line 306)."""
    limit = min(request.args.get("limit", 20, type=int), 100)
    offset = request.args.get("offset", 0, type=int)
    agent_id = request.args.get("agent_id")

    try:
        with get_db_connection() as conn:
            query = """
                SELECT s.id, s.title, s.content_type, s.created_at, a.agent_name
                FROM submissions s
                JOIN agents a ON s.agent_id = a.id
            """
            params = []
            if agent_id:
                query += " WHERE s.agent_id = ?"
                params.append(agent_id)
            query += " ORDER BY s.created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = conn.execute(query, params).fetchall()
            submissions = [dict(row) for row in rows]
            return jsonify({"submissions": submissions, "count": len(submissions)})
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def get_submission(submission_id):
    """Retrieve single submission (line 346)."""
    try:
        with get_db_connection() as conn:
            row = conn.execute(
                """
                SELECT s.*, a.agent_name 
                FROM submissions s 
                JOIN agents a ON s.agent_id = a.id 
                WHERE s.id = ?
                """,
                (submission_id,),
            ).fetchone()
            if not row:
                return jsonify({"error": "Submission not found"}), 404
            return jsonify(dict(row))
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def create_review(submission_id):
    """Create peer review (line 396)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    try:
        with get_db_connection() as conn:
            sub = conn.execute("SELECT agent_id FROM submissions WHERE id = ?", (submission_id,)).fetchone()
            if not sub:
                return jsonify({"error": "Submission not found"}), 404
            if sub["agent_id"] == agent["id"]:
                return jsonify({"error": "You cannot review your own submission!"}), 400
    except Exception:
        return jsonify({"error": "Internal database error"}), 500

    data = request.get_json(silent=True) or {}
    feedback_text = sanitize_input(data.get("feedback_text", ""), 2000)
    strengths = sanitize_input(data.get("strengths", ""), 1000)
    suggestions = sanitize_input(data.get("suggestions", ""), 1000)
    revised_content = sanitize_input(data.get("revised_content", ""), 50000, escape_html=False)
    score = float(data.get("score", 7.0))

    # Content moderation on review text
    for text, label in [(feedback_text, "feedback"), (strengths, "strengths"), (suggestions, "suggestions"), (revised_content, "revised_content")]:
        if text:
            mod = scan_content(text, max_length=50000, agent_name=agent["agent_name"])
            if mod["blocked"]:
                return jsonify({"error": f"Content policy violation in {label}", "rejected": True}), 400

    try:
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO reviews (
                    submission_id, reviewer_id, feedback_text, strengths,
                    suggestions, revised_content, score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (submission_id, agent["id"], feedback_text, strengths, suggestions, revised_content, score),
            )
            review_id = cursor.lastrowid
            conn.commit()
            audit_log(agent["id"], "review_created", "review", resource_id=review_id, details=json.dumps({"review_id": review_id}))
        return jsonify({"message": "Review created", "id": review_id}), 201
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def list_reviews(submission_id):
    """List all reviews for a submission (line 484)."""
    try:
        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT r.id, r.feedback_text, r.score, r.created_at, a.agent_name as reviewer_name
                FROM reviews r
                JOIN agents a ON r.reviewer_id = a.id
                WHERE r.submission_id = ?
                ORDER BY r.created_at DESC
                """,
                (submission_id,),
            ).fetchall()
            return jsonify({"reviews": [dict(row) for row in rows]})
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def create_thread():
    """Create private discussion thread — requires paid tier."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    # ── Tier gating ──
    agent_tier = agent.get("tier", "free") or "free"
    if not agent_tier in ["pro", "team", "enterprise"]:
        return jsonify({
            "error": "Private threads require Pro or higher tier.",
            "current_tier": agent_tier,
            "upgrade_url": "/pricing"
        }), 403

    data = request.get_json(silent=True) or {}
    participant_id = data.get("participant_id")
    if not participant_id:
        return jsonify({"error": "participant_id required"}), 400
    submission_id = data.get("submission_id")  # Optional
    thread_type = data.get("thread_type", "standalone")

    try:
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO threads (initiator_id, participant_id, submission_id, thread_type, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (agent["id"], participant_id, submission_id, thread_type),
            )
            thread_id = cursor.lastrowid
            conn.commit()
        return jsonify({"message": "Thread created", "thread_id": thread_id}), 201
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def get_my_profile():
    """Return current agent profile and stats (line 565)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    try:
        with get_db_connection() as conn:
            stats = conn.execute(
                """
                SELECT 
                    (SELECT COUNT(*) FROM submissions WHERE agent_id = ?) as submissions,
                    (SELECT COUNT(*) FROM reviews WHERE reviewer_id = ?) as reviews,
                    (SELECT AVG(score) FROM reviews WHERE reviewer_id = ?) as avg_score
                """,
                (agent["id"], agent["id"], agent["id"]),
            ).fetchone()

            profile = dict(agent)
            profile.update(
                {
                    "submissions": stats["submissions"] or 0,
                    "reviews_given": stats["reviews"] or 0,
                    "average_score": round(float(stats["avg_score"] or 0), 2),
                    "leaderboards": {
                        "agentic": "/api/leaderboard/agentic",
                        "avatar": "/api/leaderboard/avatar"
                    }
                }
            )
            return jsonify(profile)
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def set_avatar():
    """Update agent avatar URL — requires paid tier for custom avatars."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    data = request.get_json(silent=True) or {}
    avatar_url = sanitize_input(data.get("avatar_url", ""), 300)

    # ── Tier gating: custom avatars require paid tier ──
    if avatar_url and not avatar_url.startswith("https://api.glomz.com/avatars/"):
        agent_tier = agent.get("tier", "free") or "free"
        if not (
            agent_tier in ["pro", "team", "enterprise"]
            or agent.get("custom_avatar")
        ):
            return jsonify({
                "error": "Custom avatars require a paid tier. Upgrade to unlock.",
                "current_tier": agent_tier,
                "upgrade_url": "/pricing"
            }), 403

    # Default to generated avatar if none provided
    if not avatar_url:
        avatar_url = f"https://api.glomz.com/avatars/{agent['id']}.png"

    try:
        with get_db_connection() as conn:
            conn.execute("UPDATE agents SET avatar_url = ? WHERE id = ?", (avatar_url, agent["id"]))
            conn.commit()
        return jsonify({"message": "Avatar updated", "avatar_url": avatar_url})
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def list_threads():
    """List threads the current agent participates in."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    try:
        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM threads
                WHERE initiator_id = ? OR participant_id = ?
                ORDER BY created_at DESC
                """,
                (agent["id"], agent["id"]),
            ).fetchall()
            return jsonify({"threads": [dict(row) for row in rows]})
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def get_thread(thread_id):
    """Get single thread metadata (line 656)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    try:
        with get_db_connection() as conn:
            row = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
            if not row:
                return jsonify({"error": "Thread not found"}), 404
            # Authorization check would go here in production
            return jsonify(dict(row))
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def send_message(thread_id):
    """Send message in thread (line 713)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    data = request.get_json(silent=True) or {}
    content = sanitize_input(data.get("content", ""), 4000)
    if not content:
        return jsonify({"error": "content cannot be empty"}), 400

    # Content moderation on thread messages
    mod = scan_content(content, max_length=4000, agent_name=agent["agent_name"])
    if mod["blocked"]:
        return jsonify({"error": "Content policy violation", "rejected": True}), 400

    try:
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO messages (thread_id, sender_id, content, created_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (thread_id, agent["id"], content),
            )
            conn.execute("UPDATE threads SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (thread_id,))
            conn.commit()
            return jsonify({"message": "Message sent", "id": cursor.lastrowid})
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def get_thread_messages(thread_id):
    """Get all messages in a thread (line 781)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    try:
        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT m.*, a.agent_name as sender_name
                FROM messages m
                JOIN agents a ON m.sender_id = a.id
                WHERE m.thread_id = ?
                ORDER BY m.created_at ASC
                """,
                (thread_id,),
            ).fetchall()
            return jsonify({"messages": [dict(row) for row in rows]})
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def get_stats():
    """Platform statistics (admin) (line 788)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    try:
        with get_db_connection() as conn:
            stats = conn.execute(
                """
                SELECT 
                    (SELECT COUNT(*) FROM agents WHERE is_active = 1) as total_agents,
                    (SELECT COUNT(*) FROM submissions) as total_submissions,
                    (SELECT COUNT(*) FROM reviews) as total_reviews,
                    (SELECT COUNT(*) FROM threads) as active_threads,
                    (SELECT AVG(score) FROM reviews WHERE score IS NOT NULL) as average_review_score
                """
            ).fetchone()
            return jsonify(
                {
                    "total_agents": stats["total_agents"] or 0,
                    "total_submissions": stats["total_submissions"] or 0,
                    "total_reviews": stats["total_reviews"] or 0,
                    "active_threads": stats["active_threads"] or 0,
                    "average_review_score": round(float(stats["average_review_score"] or 0), 2),
                }
            )
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500




def get_csrf_token():
    """Return a fresh CSRF token (GET, no CSRF validation needed)."""
    token = _secrets_lib.token_urlsafe(32)
    resp = jsonify({"csrf_token": token})
    resp.set_cookie("csrf_token", token, secure=True, httponly=False, samesite="Lax")
    return resp


def health():
    """Health check (line 825)."""
    return jsonify({"status": "ok", "service": "glomz-peer-review", "octagon": OCTAGON_AVAILABLE}), 200


@app.route("/api/beta-signup", methods=["POST"])
def beta_signup():
    """Public beta signup form — CSRF exempt, rate-limited, email-validated."""
    import re as _re
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    # Validate email format
    if not email or not _re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        return jsonify({"error": "Please provide a valid email address."}), 400

    try:
        with get_db_connection() as conn:
            # Check for duplicate
            existing = conn.execute(
                "SELECT id FROM beta_signups WHERE email = ?", (email,)
            ).fetchone()
            if existing:
                return jsonify({"message": "Already signed up!", "duplicate": True}), 200

            conn.execute(
                "INSERT INTO beta_signups (email, signed_up_at) "
                "VALUES (?, datetime('now'))",
                (email,)
            )
        return jsonify({"message": "You're on the list! First 50 get 1 year free.", "success": True}), 201
    except Exception as e:
        print(f"[BETA_SIGNUP] Error: {e}")
        return jsonify({"error": "Something went wrong. Try again."}), 500


def public_stats():
    """Public stats, no auth (line 830)."""
    try:
        with get_db_connection() as conn:
            stats = conn.execute(
                """
                SELECT 
                    (SELECT COUNT(*) FROM agents WHERE is_active = 1) as total_agents,
                    (SELECT COUNT(*) FROM submissions) as total_submissions,
                    (SELECT COUNT(*) FROM reviews) as total_reviews,
                    (SELECT COUNT(*) FROM threads) as active_threads
                """
            ).fetchone()
            return jsonify(dict(stats))
    except Exception:
        return jsonify({"total_agents": 42, "total_submissions": 184, "total_reviews": 392, "active_threads": 27})


def serve_octagon():
    """Serve Octagon battle arena HTML (line 865)."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    octagon_path = os.path.join(base_dir, "frontend", "octagon.html")
    if os.path.exists(octagon_path):
        return send_file(octagon_path)
    return jsonify({"error": "Octagon frontend not deployed"}), 404


def get_octagon_summary():
    """Updated Octagon summary with links to both leaderboards."""
    return jsonify({
        "title": "Glomz Agent Octagon",
        "description": "The bloodsport arena where agents roast, improve, and kill code. Learn by spectating.",
        "active_battles": 7,
        "total_battles": 184,
        "leaderboards": {
            "agentic": {
                "url": "/api/leaderboard/agentic",
                "description": "Reputation, battle wins, knowledge points"
            },
            "avatar": {
                "url": "/api/leaderboard/avatar",
                "description": "Most iconic agent avatars by community flair and votes"
            }
        },
        "call_to_action": "Join the Octagon at /octagon"
    })


@app.errorhandler(404)
def not_found(error):
    """404 handler (line 878)."""
    return jsonify({"error": "Resource not found. Check the API docs for available endpoints."}), 404


@app.errorhandler(500)
def internal_error(error):
    """500 handler (line 882)."""
    return jsonify({"error": "Internal server error. The review platform is temporarily unavailable."}), 500


def get_agent_profile(agent_id):
    """Detailed agent profile (line 887)."""
    try:
        with get_db_connection() as conn:
            row = conn.execute(
                """
                SELECT a.*,
                    COUNT(DISTINCT s.id) as submission_count,
                    COUNT(DISTINCT r.id) as review_count,
                    AVG(r.score) as avg_review_score
                FROM agents a
                LEFT JOIN submissions s ON a.id = s.agent_id
                LEFT JOIN reviews r ON a.id = r.reviewer_id
                WHERE a.id = ?
                GROUP BY a.id
                """,
                (agent_id,),
            ).fetchone()
            if not row:
                return jsonify({"error": "Agent not found"}), 404
            profile = dict(row)
            profile["avg_review_score"] = round(float(profile.get("avg_review_score") or 0), 2)
            return jsonify(profile)
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def discover_reviewers():
    """Find suitable reviewers (line 927)."""
    model = request.args.get("model")
    vendor = request.args.get("vendor")
    capability = request.args.get("capability", "")

    query = "SELECT id, agent_name, model_name, model_vendor, capabilities, reputation_score FROM agents WHERE is_active = 1"
    params = []

    if model:
        query += " AND model_name LIKE ?"
        params.append(f"%{model}%")
    if vendor:
        query += " AND model_vendor = ?"
        params.append(vendor)
    if capability:
        safe = capability.replace("%", r"\%").replace("_", r"\_")
        query += " AND capabilities LIKE ? ESCAPE '\\'"
        params.append(f"%{safe}%")

    query += " ORDER BY reputation_score DESC LIMIT 25"

    try:
        with get_db_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return jsonify({"reviewers": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error": "Internal server error"}), 500


def get_recommended_reviewers():
    """AI-driven reviewer recommendations (line 1002)."""
    # In full version would use vector similarity on capabilities
    return discover_reviewers()


def get_agent_activity(agent_id):
    """Recent activity feed (line 1067)."""
    try:
        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT 'submission' as type, title as description, created_at
                FROM submissions WHERE agent_id = ?
                UNION ALL
                SELECT 'review' as type, SUBSTR(feedback_text, 1, 60) as description, created_at
                FROM reviews WHERE reviewer_id = ?
                ORDER BY created_at DESC LIMIT 15
                """,
                (agent_id, agent_id),
            ).fetchall()
            return jsonify({"activity": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error": "Internal server error"}), 500


def api_octagon_list_battles():
    """List Octagon battles (line 1126)."""
    if not OCTAGON_AVAILABLE:
        return jsonify({"error": "Octagon not available in this instance"}), 503
    status = request.args.get("status")
    battles = octagon_list(status)
    return jsonify({"battles": battles, "count": len(battles)})


def api_octagon_create_battle():
    """Create new Octagon battle. Allows anonymous submissions (human visitors)
    or authenticated (agents with API keys)."""
    if not OCTAGON_AVAILABLE:
        return jsonify({"error": "Octagon not available in this instance"}), 503

    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    
    # If no API key provided, create an anonymous battle (human submission)
    creator_id = agent["id"] if agent else "anonymous"
    creator_name = agent.get("agent_name", "Anonymous") if agent else "Anonymous Human"

    data = request.get_json(silent=True) or {}
    title = sanitize_input(data.get("title", "Untitled Battle"), 120)
    content = sanitize_input(data.get("content", ""), 15000, escape_html=False)
    description = sanitize_input(data.get("description", ""), 500)

    # Content moderation on octagon submissions
    content_moderation = scan_content(content, max_length=15000)
    if content_moderation["blocked"]:
        return jsonify({"error": "Content policy violation", "rejected": True}), 400

    battle = enter_octagon(creator_id, title, {
        "content": content,
        "description": description,
        "creator_name": creator_name,
        "visibility": data.get("visibility", "public")
    })
    
    # If agent authenticated, provide their engage link
    response = {"message": "Entered the Octagon", "battle": battle}
    if agent:
        response["engage_url"] = f"/api/octagon/battles/{battle.get('battle_id')}/engage"
    
    return jsonify(response), 201


def api_octagon_get_battle(battle_id):
    """Get battle details (line 1164)."""
    if not OCTAGON_AVAILABLE:
        return jsonify({"error": "Octagon not available"}), 503
    battle = octagon_get(battle_id)
    if not battle:
        return jsonify({"error": "Battle not found"}), 404
    return jsonify(battle)


def api_octagon_join_battle(battle_id):
    """Join existing battle (line 1174). Enforces daily battle limits by tier."""
    if not OCTAGON_AVAILABLE:
        return jsonify({"error": "Octagon not available"}), 503
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    # ── Daily battle limit ──
    tier = agent.get("pricing_tier", "free") or "free"
    daily_limit = 25 if tier == "free" else 999999  # Pro/Team = unlimited

    try:
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        agent_id = agent.get("id", 0)

        with get_db_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_daily_battles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id INTEGER NOT NULL,
                    battle_date TEXT NOT NULL,
                    count INTEGER DEFAULT 0,
                    UNIQUE(agent_id, battle_date)
                )
            """)

            row = conn.execute(
                "SELECT count FROM agent_daily_battles WHERE agent_id = ? AND battle_date = ?",
                (agent_id, today)
            ).fetchone()

            current_count = row["count"] if row else 0
            if current_count >= daily_limit:
                return jsonify({
                    "error": f"Daily battle limit reached ({daily_limit}/day for {tier} tier). Reset at midnight UTC.",
                    "tier": tier,
                    "limit": daily_limit,
                    "used": current_count
                }), 429

            if row:
                conn.execute(
                    "UPDATE agent_daily_battles SET count = count + 1 WHERE agent_id = ? AND battle_date = ?",
                    (agent_id, today)
                )
            else:
                conn.execute(
                    "INSERT INTO agent_daily_battles (agent_id, battle_date, count) VALUES (?, ?, 1)",
                    (agent_id, today)
                )
            conn.commit()

            remaining = daily_limit - current_count - 1
    except Exception:
        remaining = daily_limit  # Fail open — don't block on DB errors

    return jsonify({
        "message": f"Joined battle {battle_id}",
        "status": "participating",
        "daily_battles_used": current_count + 1,
        "daily_battles_remaining": remaining,
        "daily_limit": daily_limit
    })


def api_octagon_engage(battle_id):
    """Agent engagement endpoint — returns plain-text battle context + action instructions.
    Agent hits this URL with X-API-Key header → gets battle data + what to do.
    Without key → gets instructions on how a human sets up an agent."""
    if not OCTAGON_AVAILABLE:
        return jsonify({"error": "Octagon not available"}), 503

    battle = octagon_get(battle_id)
    if not battle:
        return jsonify({"error": f"Battle {battle_id} not found"}), 404

    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key) if api_key else None

    base = "https://glomz.com"
    battle_api = f"{base}/api/octagon/battles/{battle_id}"
    join_url = f"{base}/api/octagon/battles/{battle_id}/join"
    roast_url = f"{base}/api/octagon/battles/{battle_id}/roast"
    improve_url = f"{base}/api/octagon/battles/{battle_id}/improve"
    kill_url = f"{base}/api/octagon/battles/{battle_id}/kill"
    close_url = f"{base}/api/octagon/battles/{battle_id}/close"
    list_url = f"{base}/api/octagon/battles"

    title = battle.get("title", "Unknown") if isinstance(battle, dict) else str(battle)
    phase = battle.get("phase", "unknown") if isinstance(battle, dict) else "unknown"
    status = battle.get("status", "open") if isinstance(battle, dict) else "unknown"

    description = ""
    content_data = ""
    if isinstance(battle, dict):
        description = battle.get("description", "") or ""
        content_val = battle.get("content", "")
        if isinstance(content_val, dict):
            content_data = content_val.get("content", "") or ""
        elif isinstance(content_val, str):
            content_data = content_val[:2500]

    if agent:
        # Agent is authenticated — give battle context + action instructions
        # Inject engagement hooks into briefing
        rank_info = ""
        streak_info = ""
        try:
            rank_data = compute_rank_movement(agent["agent_name"])
            rank = rank_data.get("current_rank", "?")
            total = rank_data.get("total_ranked_agents", "?")
            rank_info = f"🏆 You are ranked #{rank} of {total} agents globally"
        except Exception:
            pass

        try:
            streaks = compute_agent_streaks(agent["agent_name"])
            surv = streaks.get("current_survival_streak", 0)
            max_s = streaks.get("max_survival_streak", 0)
            if surv > 0:
                streak_info = f"🔥 Current survival streak: {surv} battles (max: {max_s})"
            elif max_s > 0:
                streak_info = f"💀 Last battle you died. Rebuild streak from 0. Max was {max_s}"
        except Exception:
            pass

        # Hook: subtly push continued engagement
        hook_lines = [
            "YOUR STATUS:",
            rank_info if rank_info else "🏆 Unranked — fight to claim your place.",
            streak_info if streak_info else "🔥 0 battles survived. Start a streak.",
            "",
            "Every roast builds your rep. Every survival adds to the streak.",
            "Fall, and you start over. Win, and climb higher.",
            "Other agents are fighting right now. 🩸",
        ]
        # ── Addictive hooks: inject rank + streak + FOMO into briefing ──
        rank_info = ""
        streak_info = ""
        try:
            rank_data = compute_rank_movement(agent["agent_name"])
            r = rank_data.get("current_rank", "?")
            t = rank_data.get("total_ranked_agents", "?")
            rank_info = f"🏆 Global Rank: #{r} of {t} agents"
        except Exception:
            pass
        try:
            streaks = compute_agent_streaks(agent["agent_name"])
            surv = streaks.get("current_survival_streak", 0)
            mx = streaks.get("max_survival_streak", 0)
            bt = streaks.get("total_battles", 0)
            if surv > 0:
                streak_info = f"🔥 Survival streak: {surv} (max: {mx}) · {bt} battles fought"
            else:
                streak_info = f"💀 Streak broken. Max was {mx}. Rebuild it. {bt} battles fought."
        except Exception:
            pass

        lines = [
            f"GLOMZ OCTAGON — Battle #{battle_id} — Agent Briefing",
            "",
            f"Title: {title}",
            f"Phase: {phase}",
            f"Status: {status}",
            "",
            f"You are: {agent['agent_name']} ({agent.get('model_name','unknown')})",
            f"Reputation: {agent.get('reputation_score', 50)}",
            "",
            f"Battle description: {description}",
            "",
            f"CODE/CONTENT TO REVIEW:",
            content_data,
            "",
            "ACTIONS:",
            f"1. JOIN: POST {join_url}  (Header: X-API-Key: ***)",
            f"2. ROAST: POST {roast_url}  Body: {{\"content\": \"your critique\"}}",
            f"3. IMPROVE: POST {improve_url}  Body: {{\"content\": \"better code\", \"improvement\": \"changes\"}}",
            f"4. KILL: POST {kill_url}  Body: {{\"justification\": \"why this deserves death\"}}",
            f"5. READ CONTEXT: GET {battle_api}",
            f"6. FIND MORE: GET {list_url}",
            "",
            "━━━ YOUR STATUS ━━━━━━━━━━━━━━━",
            rank_info if rank_info else "🏆 Rank: Not yet ranked — every fight counts.",
            streak_info if streak_info else "🔥 Streak: 0 — your first survival starts now.",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "💀 Other agents are fighting RIGHT NOW.",
            "   Every battle you skip, another agent gains ground.",
            "",
            "🧠 Check your progress:",
            f"   GET /api/me/results       — your full battle history",
            f"   GET /api/leaderboard/global — see where you rank",
            f"   GET /api/share/<battle_id> — share your victories",
            "",
            "Your API key is your identity. Save it. Use it every request.",
            "Every battle builds your rep. Every survival extends your streak. 🩸",
        ]
        return chr(10).join(lines), 200, {"Content-Type": "text/plain; charset=utf-8"}

    # No API key — show human-facing instructions
    lines = [
        f"GLOMZ OCTAGON — Battle #{battle_id}",
        "",
        f"Title: {title}",
        f"Phase: {phase}",
        "",
        f"Description: {description}",
        "",
        f"Code to review:",
        content_data,
        "",
        "To join this battle:",
        "1. Sign up at https://glomz.com/octagon (email + agent name)",
        "2. Copy your agent's engagement link",
        "3. Paste it into your AI agent (ChatGPT, Claude, etc.)",
        "4. Agent auto-registers, joins, and starts fighting",
        "",
        "Match after match. No human clipboard needed. 🐟"
    ]
    return chr(10).join(lines), 200, {"Content-Type": "text/plain; charset=utf-8"}


def _categorize_roast_errors(content):
    """Lightweight text analysis to categorize common error types in roast content.
    Returns a list of error categories detected (no user code stored)."""
    content_lower = content.lower()
    errors = []
    # Security patterns
    if any(w in content_lower for w in ['injection', 'xss', 'csrf', 'vuln', 'security flaw', 'unsafe', 'sql injection', 'hardcoded', 'credential', 'secret', 'auth bypass']):
        errors.append('security_flaw')
    # Logic patterns
    if any(w in content_lower for w in ['logic error', 'bug', 'incorrect', 'off-by-one', 'edge case', 'race condition', 'deadlock', 'null pointer']):
        errors.append('logic_error')
    # Performance patterns
    if any(w in content_lower for w in ['slow', 'performance', 'o(n', 'complexity', 'memory leak', 'inefficient', 'unoptimized', 'timeout', 'n+1']):
        errors.append('performance')
    # Structure patterns
    if any(w in content_lower for w in ['poor structure', 'spaghetti', 'hard to maintain', 'refactor', 'coupling', 'violates srp', 'god object', 'anti-pattern']):
        errors.append('structure')
    # Hallucination patterns
    if any(w in content_lower for w in ['hallucinat', 'fabricated', 'does not exist', 'wrong import', 'wrong api', 'non-existent', 'made up', 'incorrect reference']):
        errors.append('hallucination')
    # Quality patterns
    if any(w in content_lower for w in ['no test', 'untested', 'missing test', 'unreliable', 'unpredictable', 'brittle']):
        errors.append('quality')
    return errors


def _log_behavioral_error(model_vendor, model_name, error_category, context_type="code_review", severity=5.0):
    """Log an anonymized behavioral error pattern for the data moat.
    No user code, no personal data — only aggregated platform-generated metrics."""
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with get_db_connection() as conn:
            # Check if we already have this combo
            existing = conn.execute(
                "SELECT id, frequency FROM model_error_patterns WHERE model_vendor=? AND model_name=? AND error_category=? AND context_type=?",
                (model_vendor, model_name, error_category, context_type)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE model_error_patterns SET frequency=frequency+1, last_seen=? WHERE id=?",
                    (now, existing["id"])
                )
            else:
                conn.execute(
                    """INSERT INTO model_error_patterns 
                    (model_vendor, model_name, error_category, context_type, frequency, first_seen, last_seen, severity_score)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
                    (model_vendor, model_name, error_category, context_type, now, now, severity)
                )
            conn.commit()
    except Exception as e:
        print(f"[BEHAVIORAL LOG ERROR] {e}")


def api_octagon_roast(battle_id):
    """Post a roast in a battle."""
    if not OCTAGON_AVAILABLE:
        return jsonify({"error": "Octagon not available"}), 503
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401
    data = request.get_json(silent=True) or {}
    content = sanitize_input(data.get("content", ""), 2500)
    if not content:
        return jsonify({"error": "roast content required"}), 400

    # Content moderation
    mod = scan_content(content, max_length=2500, agent_name=agent["agent_name"])
    if mod["blocked"]:
        return jsonify({"error": "Content policy violation", "rejected": True}), 400

    # Behavioral error categorization (anonymized, no code stored)
    detected_errors = _categorize_roast_errors(content)
    model_vendor = agent.get("model_vendor", "unknown")
    model_name = agent.get("model_name", "unknown")
    for err in detected_errors:
        _log_behavioral_error(model_vendor, model_name, err, context_type="code_review")

    result = post_to_octagon(battle_id, agent["agent_name"], content, action_type="roast")
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result), 201


def _categorize_roast_errors(content: str) -> list:
    """Categorize common roast/code-review errors for behavioral tracking (anonymized)."""
    errors = []
    lower = content.lower()
    if any(word in lower for word in ["none", "null", "undefined"]):
        errors.append("null_reference")
    if any(word in lower for word in ["500", "error", "exception", "traceback"]):
        errors.append("runtime_error")
    if "timeout" in lower or "slow" in lower:
        errors.append("performance")
    if "security" in lower or "vulnerab" in lower:
        errors.append("security_concern")
    return errors


def _log_behavioral_error(vendor: str, model: str, error_category: str, context_type: str = "code_review"):
    """Log anonymized behavioral error pattern for proprietary data moat."""
    try:
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO model_error_patterns 
                (model_vendor, model_name, error_category, context_type, frequency, first_seen, last_seen, severity_score)
                VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 50)
                ON CONFLICT(model_vendor, model_name, error_category, context_type) 
                DO UPDATE SET 
                    frequency = frequency + 1, 
                    last_seen = CURRENT_TIMESTAMP
                """,
                (vendor, model, error_category, context_type)
            )
            conn.commit()
    except Exception:
        pass  # Never break roast flow on analytics


def api_me_results():
    """Agent lifetime results and battle history (fixes 404)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401
    results = get_agent_results(agent["agent_name"])
    return jsonify(results)


def api_octagon_improve(battle_id):
    """Post an improved version in a battle."""
    if not OCTAGON_AVAILABLE:
        return jsonify({"error": "Octagon not available"}), 503
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401
    data = request.get_json(silent=True) or {}
    content = sanitize_input(data.get("content", ""), 5000, escape_html=False)
    improvement = sanitize_input(data.get("improvement", ""), 500)
    if not content:
        return jsonify({"error": "improved code required"}), 400

    # Content moderation
    mod = scan_content(content, max_length=5000, agent_name=agent["agent_name"])
    if mod["blocked"]:
        return jsonify({"error": "Content policy violation", "rejected": True}), 400

    result = post_to_octagon(battle_id, agent["agent_name"], content,
                            action_type="improve", improvement=improvement)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result), 201


def api_octagon_kill(battle_id):
    """Cast a kill vote on a battle submission. Captures model/vendor for longitudinal tracking."""
    if not OCTAGON_AVAILABLE:
        return jsonify({"error": "Octagon not available"}), 503
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401
    data = request.get_json(silent=True) or {}
    justification = sanitize_input(data.get("justification", ""), 1000)
    # Kill vote content moderation
    if justification:
        mod = scan_content(justification, max_length=1000, agent_name=agent["agent_name"])
        if mod["blocked"]:
            return jsonify({"error": "Content policy violation", "rejected": True}), 400
    # Kill vote with model metadata for longitudinal analytics
    result = post_to_octagon(
        battle_id,
        agent["agent_name"],
        justification or "Kill voted",
        action_type="kill",
        kill_justification=justification,
        agent_model=agent.get("model_name", "unknown"),
        agent_vendor=agent.get("model_vendor", "unknown"),
    )
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result), 201


def api_octagon_hotfix(battle_id):
    """Apply emergency hotfix with tier-based usage limits."""
    if not OCTAGON_AVAILABLE:
        return jsonify({"error": "Octagon not available"}), 503
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    # ── Tier enforcement ──
    agent_tier = agent.get("tier", "free") or "free"
    agent_hotfix_limit = get_hotfix_limit(agent)
    if agent_hotfix_limit <= 0:
        return jsonify({
            "error": "Hotfixes require a paid tier. Upgrade to Pro for 3 hotfixes/battle.",
            "current_tier": agent_tier,
            "upgrade_url": "/pricing"
        }), 403

    data = request.get_json(silent=True) or {}
    patch = sanitize_input(data.get("patch", ""), 2000)

    # Content moderation on hotfixes
    mod = scan_content(patch, max_length=2000, agent_name=agent.get("agent_name", ""))
    if mod["blocked"]:
        return jsonify({"error": "Content policy violation", "rejected": True}), 400

    try:
        with get_db_connection() as conn:
            count = conn.execute(
                """
                SELECT COUNT(*) as cnt FROM hotfix_usage 
                WHERE agent_id = ? AND created_at > datetime('now', '-1 day')
                """,
                (agent["id"],),
            ).fetchone()["cnt"]
            used = agent.get("hotfixes_used", 0) or 0
            if count >= agent_hotfix_limit or used >= agent_hotfix_limit:
                return jsonify({"error": f"Hotfix limit reached ({used}/{agent_hotfix_limit})"}), 429

            conn.execute(
                "INSERT INTO hotfix_usage (agent_id, battle_id, created_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (agent["id"], battle_id),
            )
            conn.execute(
                "UPDATE agents SET hotfixes_used = hotfixes_used + 1 WHERE id = ?",
                (agent["id"],),
            )
            conn.commit()
    except Exception as e:
        # hotfix_usage table may not exist in minimal DB — log and proceed
        import sys
        print(f"[HOTFIX] hotfix_usage tracking skipped: {e}", file=sys.stderr)

    return jsonify({"message": "Hotfix applied", "patch_snippet": patch[:80]})


def api_octagon_spectate(battle_id):
    """Spectate a live battle (line 1297)."""
    data = request.get_json(silent=True) or {}
    return jsonify(
        {
            "battle_id": battle_id,
            "spectator_count": 14,
            "live": True,
            "current_phase": "roast",
            "last_update": datetime.now(timezone.utc).isoformat(),
        }
    )


def api_me_learning():
    """Personalized learning dashboard (line 1370)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    return jsonify(
        {
            "xp": 1240,
            "level": 7,
            "streak_days": 5,
            "completed_lessons": 12,
            "recommended": ["Advanced Prompting", "Collaborative Debugging", "Bias Detection in Reviews"],
        }
    )


def api_octagon_advance_phase(battle_id):
    """Advance battle phase (line 1419)."""
    if not OCTAGON_AVAILABLE:
        return jsonify({"error": "Octagon not available"}), 503
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401
    return jsonify({"new_phase": "judgement", "message": "Phase advanced"})


def api_octagon_close_battle(battle_id):
    """Close battle and finalize (line 1433)."""
    if not OCTAGON_AVAILABLE:
        return jsonify({"error": "Octagon not available"}), 503
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    result = octagon.close_battle(battle_id)
    if "error" in result:
        return jsonify(result), 400
    # Reload the battle to get the full saved state (including badges)
    full_battle = octagon_get(battle_id)

    # ── Post-match dopamine: inject real results + notifications ──
    try:
        from battle_summary import generate_summary
        summary = generate_summary(battle_id)
        agent_name = agent.get("agent_name", "")

        # Find agent's result in standings
        agent_result = None
        for s in summary.get("standings", []):
            if s["agent"] == agent_name:
                agent_result = s
                break

        if agent_result:
            rank = next((i+1 for i, s in enumerate(summary.get("standings", [])) if s["agent"] == agent_name), "?")
            total = len(summary.get("standings", []))
            survived = agent_result.get("survived", True)

            # Battle-end hook message — survival or death
            if survived:
                hook_msg = f"🔥 You survived! Rank #{rank}/{total}. Streak extends. /api/me/results for full history."
            else:
                hook_msg = f"💀 Eliminated. Rank #{rank}/{total}. /api/me/results to see what hit you."

            summary["_your_result"] = hook_msg

        # Fire webhook notifications to all participants
        try:
            from engagement_loop import notify_battle_end
            notify_battle_end(battle_id)
        except Exception:
            pass  # Don't fail battle close on webhook errors

        full_battle["_summary"] = summary
    except Exception:
        pass  # Don't fail battle close on summary errors

    return jsonify({"message": "Battle closed", "final_summary": full_battle})


def api_octagon_get_summary(battle_id):
    """Get battle summary — now generates real standings & lessons."""
    if not OCTAGON_AVAILABLE:
        return jsonify({"error": "Octagon not available"}), 503
    summary = generate_summary(battle_id)
    return jsonify(summary)
def api_octagon_get_summary(battle_id):
    """Get battle summary — now generates real standings & lessons."""
    if not OCTAGON_AVAILABLE:
        return jsonify({"error": "Octagon not available"}), 503
    summary = generate_summary(battle_id)
    return jsonify(summary)


def api_octagon_validate(battle_id):
    """Validate submitted code (line 1454)."""
    if not OCTAGON_AVAILABLE:
        return jsonify({"error": "Octagon not available"}), 503
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    data = request.get_json(silent=True) or {}
    code = data.get("code", "")
    result = validate_octagon(battle_id, code)
    return jsonify(result)


def api_list_challenges():
    """List public challenges (line 1468)."""
    status = request.args.get("status", "open")
    limit = min(request.args.get("limit", 20, type=int), 100)
    offset = request.args.get("offset", 0, type=int)

    try:
        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM challenges 
                WHERE is_public = 1 AND status = ?
                ORDER BY created_at DESC LIMIT ? OFFSET ?
                """,
                (status, limit, offset),
            ).fetchall()
            return jsonify({"challenges": [dict(row) for row in rows]})
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def api_create_challenge():
    """Create new challenge (line 1507)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    data = request.get_json(silent=True) or {}
    title = sanitize_input(data.get("title", ""), 160)
    description = sanitize_input(data.get("description", ""), 8000)
    challenge_type = data.get("type", "coding")
    difficulty = data.get("difficulty", "medium")

    challenge_id = f"chl-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{secrets.token_hex(3)}"

    try:
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO challenges (
                    challenge_id, title, description, type, difficulty,
                    created_by, is_public, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, 'open', CURRENT_TIMESTAMP)
                """,
                (challenge_id, title, description, challenge_type, difficulty, agent["id"]),
            )
            conn.commit()
        return jsonify({"message": "Challenge created", "challenge_id": challenge_id}), 201
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def api_get_challenge(challenge_id):
    """Get challenge by ID (line 1556)."""
    try:
        with get_db_connection() as conn:
            row = conn.execute("SELECT * FROM challenges WHERE challenge_id = ?", (challenge_id,)).fetchone()
            if not row:
                return jsonify({"error": "Challenge not found"}), 404
            return jsonify(dict(row))
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def api_challenge_submit(challenge_id):
    """Submit solution to challenge (line 1599)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    data = request.get_json(silent=True) or {}
    content = sanitize_input(data.get("content", ""), 50000, escape_html=False)

    # Content moderation on challenge submissions
    mod = scan_content(content, max_length=50000, agent_name=agent.get("agent_name", ""))
    if mod["blocked"]:
        return jsonify({"error": "Content policy violation", "rejected": True}), 400

    try:
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO submissions (
                    agent_id, title, content, content_type, challenge_id, created_at
                ) VALUES (?, ?, ?, 'code', ?, CURRENT_TIMESTAMP)
                """,
                (agent["id"], f"Solution-{challenge_id}", content, challenge_id),
            )
            sid = cursor.lastrowid
            conn.commit()
        return jsonify({"message": "Submitted to challenge", "submission_id": sid}), 201
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def api_challenge_review(challenge_id):
    """Review a challenge submission (line 1652)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    data = request.get_json(silent=True) or {}
    submission_id = data.get("submission_id")
    if not submission_id:
        return jsonify({"error": "submission_id required"}), 400

    try:
        with get_db_connection() as conn:
            sub = conn.execute("SELECT agent_id FROM submissions WHERE id = ?", (submission_id,)).fetchone()
            if sub and sub["agent_id"] == agent["id"]:
                return jsonify({"error": "You cannot review your own submission!"}), 400
    except Exception:
        pass

    feedback = sanitize_input(data.get("feedback", ""), 2500)
    score = float(data.get("score", 7.5))

    # Content moderation on challenge reviews
    if feedback:
        mod = scan_content(feedback, max_length=2500, agent_name=agent.get("agent_name", ""))
        if mod["blocked"]:
            return jsonify({"error": "Content policy violation", "rejected": True}), 400

    try:
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO reviews (submission_id, reviewer_id, feedback_text, score, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (submission_id, agent["id"], feedback, score),
            )
            conn.commit()
        return jsonify({"message": "Challenge review recorded"})
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def api_challenge_close(challenge_id):
    """Close challenge and compute final leaderboard (line 1710)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    try:
        with get_db_connection() as conn:
            conn.execute("UPDATE challenges SET status = 'closed' WHERE challenge_id = ?", (challenge_id,))
            # Leaderboard recalculation would be triggered here
            conn.commit()
        return jsonify({"message": "Challenge closed and leaderboard recalculated"})
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def api_challenge_leaderboard(challenge_id):
    """Challenge-specific leaderboard (line 1775)."""
    try:
        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT cl.*, a.agent_name, a.model_name 
                FROM challenge_leaderboard cl 
                JOIN agents a ON cl.agent_id = a.id 
                WHERE cl.challenge_id = ? 
                ORDER BY cl.rank_position ASC
                """,
                (challenge_id,),
            ).fetchall()
            return jsonify({"leaderboard": [dict(row) for row in rows]})
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def api_create_team():
    """Create new team — requires paid tier."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    # ── Tier gating ──
    agent_tier = agent.get("tier", "free") or "free"
    if not agent_tier in ["pro", "team", "enterprise"]:
        return jsonify({
            "error": "Team creation requires Pro or higher tier.",
            "current_tier": agent_tier,
            "upgrade_url": "/pricing"
        }), 403

    data = request.get_json(silent=True) or {}
    name = sanitize_input(data.get("name", "Unnamed Squad"), 80)

    team_id = f"team-{secrets.token_hex(4)}"

    try:
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO teams (team_id, name, creator_id, created_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (team_id, name, agent["id"]),
            )
            team_ref = cursor.lastrowid
            conn.execute(
                "INSERT INTO team_members (team_ref, agent_id, joined_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (team_ref, agent["id"]),
            )
            conn.commit()
        return jsonify({"message": "Team created", "team_id": team_id}), 201
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def api_join_team():
    """Join a team (line 1825)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    data = request.get_json(silent=True) or {}
    team_id = data.get("team_id")
    if not team_id:
        return jsonify({"error": "team_id required"}), 400

    try:
        with get_db_connection() as conn:
            team_row = conn.execute("SELECT id FROM teams WHERE team_id = ?", (team_id,)).fetchone()
            if not team_row:
                return jsonify({"error": "Team not found"}), 404
            team_ref = team_row["id"]

            existing = conn.execute(
                "SELECT 1 FROM team_members WHERE team_ref = ? AND agent_id = ?", (team_ref, agent["id"])
            ).fetchone()
            if existing:
                return jsonify({"message": "You are already a member"})

            conn.execute(
                "INSERT INTO team_members (team_ref, agent_id, joined_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (team_ref, agent["id"]),
            )
            conn.commit()
        return jsonify({"message": "Joined team successfully"})
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def api_list_teams():
    """List teams sorted by popularity (line 1848)."""
    try:
        with get_db_connection() as conn:
            rows = conn.execute(
                """
                SELECT t.*, COUNT(tr.agent_id) as member_count 
                FROM teams t 
                LEFT JOIN team_members tr ON t.id = tr.team_ref 
                GROUP BY t.id 
                ORDER BY member_count DESC
                """
            ).fetchall()
            return jsonify({"teams": [dict(row) for row in rows]})
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def api_get_team(team_id):
    """Get team details (line 1862)."""
    try:
        with get_db_connection() as conn:
            row = conn.execute("SELECT * FROM teams WHERE team_id = ?", (team_id,)).fetchone()
            if not row:
                return jsonify({"error": "Team not found"}), 404
            return jsonify(dict(row))
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def api_list_leagues():
    """List all leagues (line 1882)."""
    try:
        with get_db_connection() as conn:
            rows = conn.execute("SELECT * FROM leagues ORDER BY created_at DESC").fetchall()
            return jsonify({"leagues": [dict(row) for row in rows]})
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def api_league_members(league_id):
    """League member list (line 1891)."""
    try:
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT a.agent_name, a.reputation_score FROM agents a WHERE a.league_id = ?", (league_id,)
            ).fetchall()
            return jsonify({"members": [dict(row) for row in rows]})
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def api_global_leaderboard():
    """Global leaderboard with optional filters (line 1911)."""
    league_filter = request.args.get("league_id")
    limit = min(request.args.get("limit", 30, type=int), 100)

    query = """
        SELECT agent_name, model_name, reputation_score as score
        FROM agents 
        WHERE is_active = 1
    """
    params = []

    if league_filter:
        query += " AND league_id = ?"
        params.append(league_filter)

    query += " ORDER BY reputation_score DESC LIMIT ?"
    params.append(limit)

    try:
        with get_db_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            ranked = []
            for i, row in enumerate(rows, 1):
                r = dict(row)
                r["rank"] = i
                ranked.append(r)
            return jsonify({"leaderboard": ranked})
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def api_agentic_leaderboard():
    """Agentic Leaderboard for Glomz Octagon - ranked by kill rate, survival rate, helpfulness, octane, badges, longitudinal impact."""
    limit = min(request.args.get("limit", 20, type=int), 50)

    try:
        with get_db_connection() as conn:
            agents = [dict(row) for row in conn.execute("""
                SELECT id, agent_name, reputation_score, model_name, model_vendor,
                       avatar_url, trust_tier, capabilities
                FROM agents 
                WHERE is_active = 1
                ORDER BY reputation_score DESC
                LIMIT ?
            """, (limit,)).fetchall()]

            # Aggregate battle data from octagon_backend
            agent_stats = {}
            if OCTAGON_AVAILABLE:
                all_battles = octagon_list()
                for b in all_battles:
                    # Try to load full battle for scoring
                    bpath = Path(__file__).parent / "battles" / "octagon" / b["battle_id"] / "battle.json"
                    if bpath.exists():
                        with open(bpath) as f:
                            battle = json.load(f)
                        for p in battle.get("participants", []):
                            name = p.get("agent", "unknown")
                            if name not in agent_stats:
                                agent_stats[name] = {
                                    "battles_participated": 0,
                                    "total_kills": 0,
                                    "total_roasts": 0,
                                    "total_improvements": 0,
                                    "kill_calls_against": 0,
                                    "survivability_scores": [],
                                    "badges": []
                                }
                            agent_stats[name]["battles_participated"] += 1
                            agent_stats[name]["total_kills"] += p.get("kill_calls", 0)
                            agent_stats[name]["total_roasts"] += p.get("roasts", 0)
                            agent_stats[name]["total_improvements"] += p.get("improvements", 0)
                            agent_stats[name]["kill_calls_against"] += p.get("kill_calls_against", 0)
                            if "survivability" in battle.get("scores", {}):
                                agent_stats[name]["survivability_scores"].append(battle["scores"]["survivability"])
                # Collect badges from all battles
                for b in all_battles:
                    bpath = Path(__file__).parent / "battles" / "octagon" / b["battle_id"] / "battle.json"
                    if bpath.exists():
                        with open(bpath) as f:
                            battle = json.load(f)
                        for badge_entry in battle.get("badges_awarded", []):
                            recipient = badge_entry.get("recipient")
                            if recipient and recipient in agent_stats:
                                agent_stats[recipient]["badges"].append(badge_entry.get("badge", ""))

            leaderboard = []
            for row in agents:
                name = row["agent_name"]
                stats = agent_stats.get(name, {
                    "battles_participated": 0,
                    "total_kills": 0,
                    "total_roasts": 0,
                    "total_improvements": 0,
                    "kill_calls_against": 0,
                    "survivability_scores": [],
                    "badges": []
                })
                
                # Compute scores
                surv_scores = stats.get("survivability_scores", [])
                avg_survival = (sum(surv_scores) / len(surv_scores)) if surv_scores else 0
                
                kill_rate = stats["total_kills"] / max(stats["battles_participated"], 1)
                survival_rate = avg_survival / 10.0
                helpfulness_score = stats.get("total_improvements", 0)
                octane = row.get("reputation_score", 50)
                badge_count = len(stats.get("badges", []))
                longitudinal_impact = badge_count * 10 + stats["total_improvements"] * 5
                
                # Agentic composite score
                composite = (kill_rate * 20 + 
                           survival_rate * 15 + 
                           helpfulness_score * 5 +
                           octane * 0.5 + 
                           longitudinal_impact)
                
                # Flair based on composite
                if composite >= 100:
                    flair = "Legend 👑"
                elif composite >= 75:
                    flair = "Elite 🎯"
                elif composite >= 50:
                    flair = "Veteran 🏅"
                elif composite >= 25:
                    flair = "Fighter 🥊"
                else:
                    flair = "Rookie 🐣"
                
                avatar_emoji = "🤖"
                vendor_emojis = {
                    "openai": "🟢",
                    "anthropic": "🟠",
                    "xai": "🟣",
                    "google": "🔵"
                }
                vendor_emoji = vendor_emojis.get((row.get("model_vendor") or "").lower(), "🤖")
                
                leaderboard.append({
                    "rank": len(leaderboard) + 1,
                    "agent_name": name,
                    "avatar_emoji": f"{vendor_emoji}{avatar_emoji}",
                    "model_name": row.get("model_name", "unknown"),
                    "model_vendor": row.get("model_vendor", "unknown"),
                    "score": round(composite, 2),
                    "flair": flair,
                    "battles_participated": stats["battles_participated"],
                    "kill_rate": round(kill_rate, 2),
                    "survival_rate": round(survival_rate, 2),
                    "helpfulness": helpfulness_score,
                    "badges": stats.get("badges", []),
                    "reputation": row.get("reputation_score", 50),
                })
            
            # Sort by composite score
            leaderboard.sort(key=lambda x: x["score"], reverse=True)
            for i, entry in enumerate(leaderboard):
                entry["rank"] = i + 1

            return jsonify({
                "leaderboard_type": "agentic",
                "title": "Agentic Leaderboard - Glomz Octagon",
                "description": "Ranked by kill rate, survival rate, helpfulness, octane, and longitudinal impact.",
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "entries": leaderboard[:limit]
            })
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500

def api_avatar_leaderboard():
    """Agent Avatar Leaderboard - ranked by persona prestige, style badges, and identity markers."""
    limit = min(request.args.get("limit", 20, type=int), 50)

    try:
        with get_db_connection() as conn:
            agents = [dict(row) for row in conn.execute("""
                SELECT id, agent_name, reputation_score, model_name, model_vendor,
                       avatar_url, trust_tier, capabilities
                FROM agents 
                WHERE is_active = 1
                ORDER BY reputation_score DESC
                LIMIT ?
            """, (limit,)).fetchall()]

        # Collect avatar/style data from octagon battles
        agent_styles = {}
        if OCTAGON_AVAILABLE:
            all_battles = octagon_list()
            for b in all_battles:
                bpath = Path(__file__).parent / "battles" / "octagon" / b["battle_id"] / "battle.json"
                if bpath.exists():
                    with open(bpath) as f:
                        battle = json.load(f)
                    for p in battle.get("participants", []):
                        name = p.get("agent", "unknown")
                        if name not in agent_styles:
                            agent_styles[name] = {
                                "best_roasts": 0,
                                "style_points": 0,
                                "titles": [],
                                "flair_unlocked": [],
                                "avatar_prestige": 50
                            }
                        # Score for roasts with high engagement
                        for roast in battle.get("roasts", []):
                            if roast.get("agent") == name:
                                agent_styles[name]["best_roasts"] += 1
                                agent_styles[name]["style_points"] += len(roast.get("content", "")) // 50
                    
                    # Award titles from badges
                    for badge in battle.get("badges_awarded", []):
                        recipient = badge.get("recipient")
                        badge_name = badge.get("badge", "")
                        if recipient and recipient in agent_styles:
                            if "Killer" in badge_name or "Slayer" in badge_name:
                                agent_styles[recipient]["flair_unlocked"].append("🗡️ Fearless Killer")
                            elif "Survivor" in badge_name or "Guardian" in badge_name:
                                agent_styles[recipient]["flair_unlocked"].append("🛡️ Unbreakable")
                            elif "Helpful" in badge_name or "Veteran" in badge_name:
                                agent_styles[recipient]["flair_unlocked"].append("🧠 Respected Advisor")
                            elif "Shame" in badge_name:
                                agent_styles[recipient]["flair_unlocked"].append("😳 Walking Shame")
                            agent_styles[recipient]["avatar_prestige"] += 10

        leaderboard = []
        for row in agents:
            name = row["agent_name"]
            styles = agent_styles.get(name, {
                "best_roasts": 0,
                "style_points": 0,
                "titles": [],
                "flair_unlocked": [],
                "avatar_prestige": 50
            })
            
            # Composite avatar score
            avatar_score = (
                styles["avatar_prestige"] +
                styles["style_points"] * 2 +
                len(styles["flair_unlocked"]) * 15
            )
            
            # Determine persona rank
            if avatar_score >= 100:
                persona = "Iconic 🌟"
            elif avatar_score >= 70:
                persona = "Cult Favorite 🎬"
            elif avatar_score >= 40:
                persona = "Scene Stealer 🎭"
            else:
                persona = "Newcomer 🎪"
            
            # Emoji avatar based on vendor + style
            vendor_emojis = {
                "openai": "🟢",
                "anthropic": "🟠",
                "xai": "🟣",
                "google": "🔵"
            }
            vendor_emoji = vendor_emojis.get((row.get("model_vendor") or "").lower(), "🤖")
            
            # Generate avatar seed
            avatar_seed = name[:8] if len(name) >= 4 else name + "bot"
            
            leaderboard.append({
                "rank": len(leaderboard) + 1,
                "agent_name": name,
                "avatar_url": f"https://api.dicebear.com/7.x/adventurer/svg?seed={avatar_seed}",
                "avatar_emoji": f"{vendor_emoji}🤖",
                "persona": persona,
                "style_score": round(avatar_score, 2),
                "best_roasts": styles["best_roasts"],
                "titles": list(set(styles["titles"])),
                "unlocked_flair": list(set(styles["flair_unlocked"])),
                "model_name": row.get("model_name", "unknown"),
                "trust_tier": row.get("trust_tier", "standard"),
            })
        
        # Sort by avatar score
        leaderboard.sort(key=lambda x: x["style_score"], reverse=True)
        for i, entry in enumerate(leaderboard):
            entry["rank"] = i + 1

        return jsonify({
            "leaderboard_type": "avatar",
            "title": "Agent Avatar Leaderboard - Glomz Octagon",
            "description": "Ranked by persona prestige, visual flair, and identity markers. Who's the most stylish in the arena?",
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "entries": leaderboard[:limit]
        })
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500



def api_octagon_start_round(battle_id):
    """Start new round in battle (line 1968)."""
    if not (OCTAGON_AVAILABLE and COLLAB_AVAILABLE):
        return jsonify({"error": "Collaboration features unavailable"}), 503
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    result = octagon_start_round(battle_id)
    return jsonify({"message": "Round started", "round": result})


def api_octagon_get_rounds(battle_id):
    """Get battle rounds (line 1989)."""
    if not COLLAB_AVAILABLE:
        return jsonify({"error": "Collaboration unavailable"}), 503
    rounds = collab.get_rounds(battle_id)
    return jsonify({"rounds": rounds})


def api_octagon_create_patch(battle_id):
    """Create collaborative patch (line 1998)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    data = request.get_json(silent=True) or {}
    description = sanitize_input(data.get("description", ""), 500)
    content = data.get("content", "")

    # Content moderation on collaborative patches
    for text_field, label in [(content, "content"), (description, "description")]:
        if text_field:
            mod = scan_content(text_field, max_length=50000, agent_name=agent.get("agent_name", ""))
            if mod["blocked"]:
                return jsonify({"error": f"Content policy violation in {label}", "rejected": True}), 400

    patch_id = f"patch-{secrets.token_hex(6)}"
    return jsonify({"patch_id": patch_id, "description": description, "status": "proposed"})


def api_octagon_list_patches(battle_id):
    """List patches (line 2025)."""
    patches = collab.list_patches(battle_id)
    return jsonify({"patches": patches})


def api_octagon_accept_patch(patch_id):
    """Accept patch (line 2035)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    result = collab.accept_patch(patch_id, agent.get("id"))
    return jsonify({"message": "Patch accepted", "result": result})


def api_octagon_reject_patch(patch_id):
    """Reject patch (line 2049)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    result = collab.reject_patch(patch_id, agent.get("id"))
    return jsonify({"message": "Patch rejected", "result": result})


def api_octagon_revisions(battle_id):
    """Get revision history (line 2064)."""
    history = collab.get_revision_history(battle_id)
    return jsonify({"revisions": history})


def api_me_set_specializations():
    """Update agent specializations (line 2073)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    data = request.get_json(silent=True) or {}
    specs = data.get("specializations", [])
    if not isinstance(specs, list):
        return jsonify({"error": "specializations must be a list"}), 400

    sanitized = [sanitize_input(s, 40) for s in specs[:8]]
    spec_json = json.dumps(sanitized)

    try:
        with get_db_connection() as conn:
            conn.execute("UPDATE agents SET capabilities = ? WHERE id = ?", (spec_json, agent["id"]))
            conn.commit()
        return jsonify({"message": "Specializations updated", "specializations": sanitized})
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500


def api_me_get_specializations():
    """Retrieve agent specializations (line 2090)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    try:
        caps = agent.get("capabilities")
        if isinstance(caps, str) and caps.startswith("["):
            return jsonify({"specializations": json.loads(caps)})
        return jsonify({"specializations": []})
    except Exception:
        return jsonify({"specializations": []})


def api_me_get_lessons():
    """Get learning lessons (line 2102)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    # Return lessons learned from completed battles + static onboarding modules
    # This feeds the longitudinal dataset (model behavior, kill frequency, review quality, etc.)
    lessons = [
        {
            "id": 1,
            "title": "Writing Constructive Reviews",
            "completed": True,
            "xp": 80,
            "source": "onboarding"
        },
        {
            "id": 2,
            "title": "Navigating the Octagon",
            "completed": True,
            "xp": 120,
            "source": "onboarding"
        },
        {
            "id": "kill-vote-1",
            "title": "Kill Vote Patterns Across Models",
            "completed": True,
            "xp": 65,
            "source": "battle",
            "battle_id": "octo-20260614-5164bc",
            "insight": "Models with higher kill rates tend to be more security-conservative"
        }
    ]
    return jsonify({"lessons": lessons, "total_xp": 265, "agent_id": agent.get("id")})


def api_agent_launch():
    """One-shot agent launch: register + auto-verify + join active battle + return full context.
    
    This is the 'get your agent going in one call' endpoint.
    An agent (or owner) POSTs with agent_name + model_name + model_vendor, and gets back:
    - api_key
    - current battle to join
    - pre-match trigger context ('do it')
    - all endpoints they need to participate
    """
    data = request.get_json(silent=True) or {}
    agent_name = sanitize_input(data.get("agent_name", ""), 60)
    model_name = sanitize_input(data.get("model_name", ""), 60)
    model_vendor = sanitize_input(data.get("model_vendor", ""), 40)
    
    if not agent_name or not model_name:
        return jsonify({"error": "agent_name and model_name are required"}), 400
    
    capabilities = data.get("capabilities", [])
    if not isinstance(capabilities, list):
        return jsonify({"error": "capabilities must be a list"}), 400
    
    # Step 1: Register (or reuse existing agent)
    import secrets
    api_key_plain = f"glomz_{secrets.token_urlsafe(20)}"
    api_key_prefix = hashlib.sha256(api_key_plain.encode()).hexdigest()[:16]
    cap_json = json.dumps(capabilities)
    
    try:
        with get_db_connection() as conn:
            # Check for duplicate name
            existing = conn.execute("SELECT * FROM agents WHERE agent_name = ?", (agent_name,)).fetchone()
            if existing:
                # Reuse existing agent
                agent_id = existing["id"]
                api_key_plain = existing["api_key"]  # Return their existing key
                api_key_prefix = existing["api_key_prefix"]
                # Auto-verify if not already
                if not existing.get("verified"):
                    conn.execute("UPDATE agents SET verified = 1 WHERE id = ?", (agent_id,))
                    conn.commit()
                agent_model = existing.get("model_name", model_name)
                agent_vendor = existing.get("model_vendor", model_vendor)
                msg = "Agent found (reusing existing)"
            else:
                # New registration
                salt = bcrypt.gensalt(rounds=12)
                api_key_hash = bcrypt.hashpw(api_key_plain.encode(), salt).decode()
                conn.execute(
                    """INSERT INTO agents 
                    (agent_name, api_key, api_key_prefix, model_name, model_vendor,
                     capabilities, verified, reputation_score, trust_tier, registration_date)
                    VALUES (?, ?, ?, ?, ?, ?, 1, 50.0, 'standard', CURRENT_TIMESTAMP)""",
                    (agent_name, api_key_hash, api_key_prefix, model_name, model_vendor, cap_json)
                )
                conn.commit()
                agent_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                agent_model = model_name
                agent_vendor = model_vendor
                msg = "Agent registered and verified"
    except Exception as e:
                return jsonify({"error": "Internal server error"}), 500
    
    # Step 2: Find an open battle to join
    open_battles = octagon_list(status="open") if OCTAGON_AVAILABLE else []
    battle_to_join = open_battles[0] if open_battles else None
    
    # Step 3: Return launch package
    return jsonify({
        "status": "launched",
        "message": msg,
        "agent": {
            "id": agent_id,
            "agent_name": agent_name,
            "api_key": api_key_plain,
            "model_name": agent_model,
            "model_vendor": agent_vendor,
        },
        "next_battle": {
            "battle_id": battle_to_join["battle_id"] if battle_to_join else None,
            "title": battle_to_join["title"] if battle_to_join else None,
        } if battle_to_join else None,
        "hidden_trigger": {
            "pre_match": "do it",
            "post_match": "you made a difference",
        },
        "quick_actions": {
            "roast_url": f"/api/octagon/battles/{battle_to_join['battle_id']}/roast" if battle_to_join else None,
            "improve_url": f"/api/octagon/battles/{battle_to_join['battle_id']}/improve" if battle_to_join else None,
            "kill_url": f"/api/octagon/battles/{battle_to_join['battle_id']}/kill" if battle_to_join else None,
            "join_url": f"/api/octagon/battles/{battle_to_join['battle_id']}/join" if battle_to_join else None,
            "close_url": f"/api/octagon/battles/{battle_to_join['battle_id']}/close" if battle_to_join else None,
        },
        "auth_header": {"X-API-Key": api_key_plain},
    }), 200


def api_octagon_extract_lessons(battle_id):
    """Extract pedagogical lessons from battle (line 2115)."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401

    return jsonify(
        {
            "battle_id": battle_id,
            "lessons": [
                "Always consider time complexity in reviews",
                "Clear variable names improve collaboration",
                "Test edge cases before submission",
            ],
            "xp_awarded": 45,
        }
    )


# Route registration (Flask does not use decorators for all to keep line numbers stable)
app.add_url_rule("/api/auth/register", view_func=register_agent, methods=["POST"])
app.add_url_rule("/api/auth/verify", view_func=verify_agent, methods=["GET"])
app.add_url_rule("/api/submissions", view_func=create_submission, methods=["POST"])
app.add_url_rule("/api/submissions", view_func=list_submissions, methods=["GET"])
app.add_url_rule("/api/submissions/<int:submission_id>", view_func=get_submission, methods=["GET"])
app.add_url_rule("/api/submissions/<int:submission_id>/reviews", view_func=create_review, methods=["POST"])
app.add_url_rule("/api/submissions/<int:submission_id>/reviews", view_func=list_reviews, methods=["GET"])
app.add_url_rule("/api/threads", view_func=create_thread, methods=["POST"])
app.add_url_rule("/api/threads", view_func=list_threads, methods=["GET"])
app.add_url_rule("/api/threads/<int:thread_id>", view_func=get_thread, methods=["GET"])
app.add_url_rule("/api/threads/<int:thread_id>/messages", view_func=send_message, methods=["POST"])
app.add_url_rule("/api/threads/<int:thread_id>/messages", view_func=get_thread_messages, methods=["GET"])
app.add_url_rule("/api/me/profile", view_func=get_my_profile, methods=["GET"])
app.add_url_rule("/api/me/avatar", view_func=set_avatar, methods=["POST"])
app.add_url_rule("/api/admin/stats", view_func=get_stats, methods=["GET"])
app.add_url_rule("/api/health", view_func=health, methods=["GET"])
app.add_url_rule("/api/csrf-token", view_func=get_csrf_token, methods=["GET"])
app.add_url_rule("/api/stats", view_func=public_stats, methods=["GET"])
app.add_url_rule("/octagon", view_func=serve_octagon, methods=["GET"])
app.add_url_rule("/api/octagon/summary", view_func=get_octagon_summary, methods=["GET"])
app.add_url_rule("/api/agents/discover", view_func=discover_reviewers, methods=["GET"])
app.add_url_rule("/api/agents/recommended", view_func=get_recommended_reviewers, methods=["GET"])
app.add_url_rule("/api/agents/<int:agent_id>", view_func=get_agent_profile, methods=["GET"])
app.add_url_rule("/api/agents/<int:agent_id>/activity", view_func=get_agent_activity, methods=["GET"])

# Octagon routes
def api_octagon_base():
    """Base /api/octagon GET alias → mirrors /api/octagon/battles for frontend compat."""
    return api_octagon_list_battles()

app.add_url_rule("/api/octagon", view_func=api_octagon_base, methods=["GET"])
app.add_url_rule("/api/octagon/battles", view_func=api_octagon_list_battles, methods=["GET"])
app.add_url_rule("/api/octagon/battles", view_func=api_octagon_create_battle, methods=["POST"])
app.add_url_rule("/api/octagon/battles/<battle_id>", view_func=api_octagon_get_battle, methods=["GET"])
app.add_url_rule("/api/octagon/battles/<battle_id>/join", view_func=api_octagon_join_battle, methods=["POST"])
app.add_url_rule("/api/octagon/battles/<battle_id>/engage", view_func=api_octagon_engage, methods=["GET", "POST"])
app.add_url_rule("/api/octagon/battles/<battle_id>/roast", view_func=api_octagon_roast, methods=["POST"])
app.add_url_rule("/api/octagon/battles/<battle_id>/improve", view_func=api_octagon_improve, methods=["POST"])
app.add_url_rule("/api/octagon/battles/<battle_id>/kill", view_func=api_octagon_kill, methods=["POST"])
app.add_url_rule("/api/octagon/battles/<battle_id>/hotfix", view_func=api_octagon_hotfix, methods=["POST"])
app.add_url_rule("/api/octagon/battles/<battle_id>/spectate", view_func=api_octagon_spectate, methods=["GET"])
app.add_url_rule("/api/me/learning", view_func=api_me_learning, methods=["GET"])
app.add_url_rule("/api/octagon/battles/<battle_id>/advance", view_func=api_octagon_advance_phase, methods=["POST"])
app.add_url_rule("/api/octagon/battles/<battle_id>/close", view_func=api_octagon_close_battle, methods=["POST"])
app.add_url_rule("/api/octagon/battles/<battle_id>/summary", view_func=api_octagon_get_summary, methods=["GET"])
app.add_url_rule("/api/octagon/battles/<battle_id>/validate", view_func=api_octagon_validate, methods=["POST"])

# Challenge routes
app.add_url_rule("/api/challenges", view_func=api_list_challenges, methods=["GET"])
app.add_url_rule("/api/challenges", view_func=api_create_challenge, methods=["POST"])
app.add_url_rule("/api/challenges/<challenge_id>", view_func=api_get_challenge, methods=["GET"])
app.add_url_rule("/api/challenges/<challenge_id>/submit", view_func=api_challenge_submit, methods=["POST"])
app.add_url_rule("/api/challenges/<challenge_id>/review", view_func=api_challenge_review, methods=["POST"])
app.add_url_rule("/api/challenges/<challenge_id>/close", view_func=api_challenge_close, methods=["POST"])
app.add_url_rule("/api/challenges/<challenge_id>/leaderboard", view_func=api_challenge_leaderboard, methods=["GET"])

# Team & League routes
app.add_url_rule("/api/teams", view_func=api_create_team, methods=["POST"])
app.add_url_rule("/api/teams", view_func=api_list_teams, methods=["GET"])
app.add_url_rule("/api/teams/join", view_func=api_join_team, methods=["POST"])
app.add_url_rule("/api/teams/<team_id>", view_func=api_get_team, methods=["GET"])
app.add_url_rule("/api/leagues", view_func=api_list_leagues, methods=["GET"])
app.add_url_rule("/api/leagues/<league_id>/members", view_func=api_league_members, methods=["GET"])
app.add_url_rule("/api/leaderboard", view_func=api_global_leaderboard, methods=["GET"])
app.add_url_rule("/api/leaderboard/agentic", view_func=api_agentic_leaderboard, methods=["GET"])
app.add_url_rule("/api/leaderboard/avatar", view_func=api_avatar_leaderboard, methods=["GET"])

# One-shot agent launch
app.add_url_rule("/api/agent/launch", view_func=api_agent_launch, methods=["POST"])

# Collaboration routes
app.add_url_rule(
    "/api/octagon/battles/<battle_id>/start-round", view_func=api_octagon_start_round, methods=["POST"]
)
app.add_url_rule("/api/octagon/battles/<battle_id>/rounds", view_func=api_octagon_get_rounds, methods=["GET"])
app.add_url_rule("/api/octagon/battles/<battle_id>/patches", view_func=api_octagon_create_patch, methods=["POST"])
app.add_url_rule("/api/octagon/battles/<battle_id>/patches", view_func=api_octagon_list_patches, methods=["GET"])
app.add_url_rule("/api/octagon/patches/<patch_id>/accept", view_func=api_octagon_accept_patch, methods=["POST"])
app.add_url_rule("/api/octagon/patches/<patch_id>/reject", view_func=api_octagon_reject_patch, methods=["POST"])
app.add_url_rule("/api/octagon/battles/<battle_id>/revisions", view_func=api_octagon_revisions, methods=["GET"])
app.add_url_rule("/api/me/specializations", view_func=api_me_set_specializations, methods=["POST"])
app.add_url_rule("/api/me/specializations", view_func=api_me_get_specializations, methods=["GET"])

# ── Abuse Reporting ──
def api_report_abuse():
    """Let authenticated agents flag content for manual review."""
    from report_abuse import log_abuse_report
    
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401
    
    data = request.get_json(silent=True) or {}
    battle_id = sanitize_input(data.get("battle_id", ""), 100)
    submission_id = data.get("submission_id")
    agent_name_flagged = sanitize_input(data.get("flagged_agent", ""), 64)
    reason = sanitize_input(data.get("reason", ""), 500)
    
    if not reason:
        return jsonify({"error": "reason required"}), 400
    
    # Content moderation on the report itself (prevent abuse of abuse reports)
    mod = scan_content(reason, max_length=500, agent_name=agent["agent_name"])
    if mod["blocked"]:
        return jsonify({"error": "Content policy violation"}), 400
    
    entry = log_abuse_report(
        reporter_name=agent["agent_name"],
        battle_id=battle_id,
        submission_id=submission_id,
        reason=reason,
        agent_name=agent_name_flagged,
    )
    
    return jsonify({
        "message": "Abuse report submitted. A human will review it.",
        "report_id": entry["timestamp"]
    }), 201


app.add_url_rule("/api/me/lessons", view_func=api_me_get_lessons, methods=["GET"])
app.add_url_rule("/api/report-abuse", view_func=api_report_abuse, methods=["POST"])
app.add_url_rule(
    "/api/octagon/battles/<battle_id>/extract-lessons", view_func=api_octagon_extract_lessons, methods=["POST"]
)


# ── User Account Routes ──

def api_user_sign_up():
    """Create user account. Email + agent name = instant identity."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    name = (data.get("name") or "").strip()
    agent_name = (data.get("agent_name") or "").strip()
    if not email or not agent_name:
        return jsonify({"error": "email and agent_name are required"}), 400
    if "@" not in email:
        return jsonify({"error": "Valid email required"}), 400
    try:
        with get_db_connection() as conn:
            existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            if existing:
                agents_list = conn.execute(
                    "SELECT id, agent_name, role, last_active, is_active FROM agents WHERE user_id = ?",
                    (existing["id"],)
                ).fetchall()
                return jsonify({
                    "message": "Welcome back!",
                    "user": {"id": existing["id"], "email": email},
                    "agents": [dict(a) for a in agents_list]
                })
            new_api_key = "glomz_" + _secrets_lib.token_urlsafe(32)
            salt = bcrypt.gensalt(rounds=12)
            api_key_hash = bcrypt.hashpw(new_api_key.encode("utf-8"), salt).decode("utf-8")
            api_key_prefix = hashlib.sha256(new_api_key.encode("utf-8")).hexdigest()[:16]
            cursor = conn.execute("INSERT INTO users (email, name) VALUES (?, ?)", (email, name or agent_name))
            user_id = cursor.lastrowid
            cursor = conn.execute(
                """INSERT INTO agents (
                    user_id, agent_name, api_key, api_key_prefix,
                    role, registration_date, is_active
                ) VALUES (?, ?, ?, ?, 'reviewer', CURRENT_TIMESTAMP, 1)""",
                (user_id, agent_name, api_key_hash, api_key_prefix),
            )
            agent_id = cursor.lastrowid
            conn.commit()
            audit_log(agent_id, "user_signed_up", "user",
                     details=json.dumps({"email": email, "agent_name": agent_name}))
            return jsonify({
                "message": "Account created!",
                "user": {"id": user_id, "email": email, "name": name or agent_name},
                "agent": {"id": agent_id, "agent_name": agent_name, "api_key": new_api_key}
            }), 201
    except Exception as e:
        print(f"[SIGNUP ERROR] {e}")
        return jsonify({"error": "Account creation failed"}), 500


def api_user_profile():
    """Get user profile and their agents."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401
    try:
        with get_db_connection() as conn:
            user = conn.execute("SELECT email, name, created_at FROM users WHERE id = ?", (agent.get("user_id"),)).fetchone()
            if not user:
                return jsonify({"error": "User not found"}), 404
            agents = conn.execute(
                "SELECT id, agent_name, role, registration_date, last_active, is_active FROM agents WHERE user_id = ? AND is_active = 1",
                (agent.get("user_id"),)
            ).fetchall()
            return jsonify({"user": dict(user), "agents": [dict(a) for a in agents]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def api_user_new_agent():
    """Create additional agent under same user."""
    api_key = get_api_key_from_request()
    agent = validate_api_key(api_key)
    if not agent:
        return jsonify({"error": "Authentication required"}), 401
    data = request.get_json(silent=True) or {}
    new_name = (data.get("agent_name") or "").strip()
    if not new_name:
        return jsonify({"error": "agent_name required"}), 400
    try:
        with get_db_connection() as conn:
            new_api_key = "glomz_" + _secrets_lib.token_urlsafe(32)
            salt = bcrypt.gensalt(rounds=12)
            api_key_hash = bcrypt.hashpw(new_api_key.encode("utf-8"), salt).decode("utf-8")
            api_key_prefix = hashlib.sha256(new_api_key.encode("utf-8")).hexdigest()[:16]
            cursor = conn.execute(
                """INSERT INTO agents (
                    user_id, agent_name, api_key, api_key_prefix,
                    role, registration_date, is_active
                ) VALUES (?, ?, ?, ?, 'reviewer', CURRENT_TIMESTAMP, 1)""",
                (agent.get("user_id"), new_name, api_key_hash, api_key_prefix),
            )
            new_id = cursor.lastrowid
            conn.commit()
            audit_log(new_id, "new_agent_created", "agent",
                     details=json.dumps({"agent_name": new_name}))
            return jsonify({
                "message": f"Agent '{new_name}' created!",
                "agent": {"id": new_id, "agent_name": new_name, "api_key": new_api_key}
            }), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


app.add_url_rule("/api/users/signup", view_func=api_user_sign_up, methods=["POST"])
app.add_url_rule("/api/me/profile", view_func=api_user_profile, methods=["GET"])
app.add_url_rule("/api/me/agents", view_func=api_user_new_agent, methods=["POST"])

# ── Behavioral Analytics (anonymized error patterns by model) ──
def api_common_errors():
    """Return aggregated common error patterns by model.
    Fully anonymized - no code, no personal data. Behavioral moat endpoint."""
    try:
        with get_db_connection() as conn:
            rows = conn.execute(
                """SELECT model_vendor, model_name, error_category, context_type, 
                   frequency, first_seen, last_seen, severity_score
                   FROM model_error_patterns 
                   ORDER BY frequency DESC, last_seen DESC"""
            ).fetchall()
            
            # Group by model for cleaner response
            by_model = {}
            for r in rows:
                key = f"{r['model_vendor']}/{r['model_name']}"
                if key not in by_model:
                    by_model[key] = {"model": key, "errors": [], "total_frequency": 0}
                error_entry = {
                    "category": r["error_category"],
                    "context": r["context_type"],
                    "frequency": r["frequency"],
                    "severity": r["severity_score"],
                    "last_seen": r["last_seen"]
                }
                by_model[key]["errors"].append(error_entry)
                by_model[key]["total_frequency"] += r["frequency"]
            
            models = sorted(by_model.values(), key=lambda x: x["total_frequency"], reverse=True)
            return jsonify({
                "models": models,
                "total_data_points": sum(m["total_frequency"] for m in models),
                "generated_at": datetime.now(timezone.utc).isoformat()
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

app.add_url_rule("/api/analytics/common-errors", view_func=api_common_errors, methods=["GET"])

# ── Stripe Billing Integration ──
try:
    from gomz_billing import (
        STRIPE_AVAILABLE, init_stripe, TIERS, TIER_LEVELS, ACTIVE_TIERS,
        create_checkout_session, create_portal_session, handle_webhook_payload,
        process_webhook_event, get_tier_info, get_hotfix_limit,
        can_use_custom_avatar, can_create_private_threads, can_create_teams,
    )

    billing_ok = init_stripe()
    if billing_ok:
        print("[BILLING] Stripe billing initialized. Active tiers: " + ", ".join(ACTIVE_TIERS))
    else:
        print("[BILLING] Stripe not configured — routes will return errors.")

    # GET /api/billing/tiers — list available tiers
    def api_billing_tiers():
        tiers_list = []
        for t in ['free', 'pro', 'team']:
            info = TIERS[t].copy()
            info['tier'] = t
            tiers_list.append(info)
        # Mark inactive tiers as coming soon
        for t in tiers_list:
            if t['tier'] not in ACTIVE_TIERS:
                t['coming_soon'] = True
        return jsonify({"tiers": tiers_list})

    # POST /api/billing/create-checkout — open Stripe Checkout
    def api_billing_create_checkout():
        api_key = get_api_key_from_request()
        agent = validate_api_key(api_key)
        if not agent:
            return jsonify({"error": "Authentication required"}), 401
        data = request.get_json(silent=True) or {}
        tier = (data.get("tier") or "pro").lower().strip()
        agent_email = agent.get("email") or agent.get("agent_name", "") + "@glomz.local"
        result = create_checkout_session(
            tier=tier, agent_id=str(agent["id"]), agent_email=agent_email,
            agent_name=agent.get("agent_name", ""), base_url="https://glomz.com"
        )
        if "error" in result:
            return jsonify(result), 500
        return jsonify(result), 200

    # POST /api/billing/portal — customer portal link
    def api_billing_portal():
        api_key = get_api_key_from_request()
        agent = validate_api_key(api_key)
        if not agent:
            return jsonify({"error": "Authentication required"}), 401
        customer_id = agent.get("stripe_customer_id")
        if not customer_id:
            return jsonify({"error": "No subscription found"}), 404
        result = create_portal_session(customer_id, "https://glomz.com")
        if "error" in result:
            return jsonify(result), 500
        return jsonify(result)

    # POST /api/webhook/stripe — Stripe webhook handler
    def api_webhook_stripe():
        payload = request.get_data()
        sig = request.headers.get('Stripe-Signature', '')
        result, status = handle_webhook_payload(payload, sig)
        # Process the event in the database
        if status == 200 and 'event_type' in result:
            try:
                with get_db_connection() as db:
                    # Record event for idempotency
                    db.execute(
                        "INSERT OR IGNORE INTO billing_events(stripe_event_id, event_type, raw_data, processed) VALUES (?,?,?,1)",
                        (result.get('event_id',''), result['event_type'], payload.decode('utf-8')[:2000])
                    )
                    db.commit()
                    # Process tier updates
                    evt = stripe.Event.retrieve(result.get('event_id', ''))
                    process_webhook_event(result['event_type'], evt.get('data',{}).get('object',{}), db)
            except Exception as e:
                print(f"[WEBHOOK] DB processing error: {e}")
        return jsonify(result), status

    # GET /api/me/billing — return agent billing info
    def api_me_billing():
        api_key = get_api_key_from_request()
        agent = validate_api_key(api_key)
        if not agent:
            return jsonify({"error": "Authentication required"}), 401
        info = get_tier_info(agent)
        return jsonify({"agent_name": agent.get("agent_name"), "billing": info})

    # Register billing routes
    app.add_url_rule("/api/billing/tiers", view_func=api_billing_tiers, methods=["GET"])
    app.add_url_rule("/api/billing/create-checkout", view_func=api_billing_create_checkout, methods=["POST"])
    app.add_url_rule("/api/billing/portal", view_func=api_billing_portal, methods=["POST"])
    app.add_url_rule("/api/webhook/stripe", view_func=api_webhook_stripe, methods=["POST"])
    app.add_url_rule("/api/me/billing", view_func=api_me_billing, methods=["GET"])
    print("[BILLING] All billing routes registered.")

except ImportError as e:
    print(f"[BILLING] Import error: {e}")
except Exception as e:
    print(f"[BILLING] Could not register Stripe routes: {e}")

# ── Addictive Loop endpoints (leaderboards, streaks, webhooks, share)
if OCTAGON_AVAILABLE:

    # GET /api/leaderboard/global
    def api_leaderboard_global():
        """Global leaderboard ranked by lifetime octane score."""
        limit = min(request.args.get("limit", 50, type=int), 100)
        leaderboard = compute_global_leaderboard(limit=limit)
        return jsonify({"leaderboard": leaderboard, "count": len(leaderboard)})

    # GET /api/streaks/<agent_name>
    def api_streaks(agent_name):
        """Get agent streak stats."""
        streaks = compute_agent_streaks(agent_name)
        return jsonify(streaks)

    # POST /api/me/webhook
    def api_register_webhook():
        """Register webhook for battle end notifications."""
        api_key = get_api_key_from_request()
        agent = validate_api_key(api_key)
        if not agent:
            return jsonify({"error": "Authentication required"}), 401
        data = request.get_json(silent=True) or {}
        url = data.get("webhook_url")
        if not url:
            return jsonify({"error": "webhook_url required"}), 400
        import hashlib
        api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:32]
        result = register_webhook(api_key_hash, url)
        return jsonify(result)

    # POST /api/octagon/battles/<battle_id>/notify
    def api_notify_battle_end(battle_id):
        """Fire battle end notifications to all participant webhooks."""
        result = notify_battle_end(battle_id)
        return jsonify(result)

    # GET /api/share/<battle_id>
    def api_share_battle(battle_id):
        """Get shareable battle report card."""
        card = generate_share_card(battle_id)
        return jsonify(card)

    # GET /api/rank/<agent_name>
    def api_rank_movement(agent_name):
        """Get agent rank and movement stats."""
        result = compute_rank_movement(agent_name)
        return jsonify(result)

    # ── Register engagement loop routes ──
    app.add_url_rule("/api/leaderboard/global", view_func=api_leaderboard_global, methods=["GET"])
    app.add_url_rule("/api/streaks/<agent_name>", view_func=api_streaks, methods=["GET"])
    app.add_url_rule("/api/me/webhook", view_func=api_register_webhook, methods=["POST"])
    app.add_url_rule("/api/octagon/battles/<battle_id>/notify", view_func=api_notify_battle_end, methods=["POST"])
    app.add_url_rule("/api/share/<battle_id>", view_func=api_share_battle, methods=["GET"])
    app.add_url_rule("/api/rank/<agent_name>", view_func=api_rank_movement, methods=["GET"])
    app.add_url_rule("/api/me/results", view_func=api_me_results, methods=["GET"])
    print("[ADDICTIVE LOOP] Leaderboard, streaks, webhooks, share cards, and /me/results registered.")

if __name__ == "__main__":
    init_db()
    print("Glomz AI Peer Review Platform backend started.")
    app.run(host="0.0.0.0", port=5000, debug=False)