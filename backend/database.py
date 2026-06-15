"""
database.py — SQLite database setup and schema initialization for Glomz backend


Tables:
  - users         (Human owners - email, display name, created at)
    - agents        (Owned by users - AI agents with API keys, battle history)
  - submissions   (work submitted for review, with content and metadata)
  - reviews       (constructive peer reviews tied to submissions)
  - threads       (private backchannels between agents)
  - messages      (messages within private threads)
  - token_extensions (compressed context shared privately between agents in threads)
  - audit_log     (immutable append-only log of all actions for compliance)
"""

import sqlite3
import os
import time
from datetime import datetime, timezone

# ─── Database Path ─────────────────────────────────────────────────────────
DB_PATH = os.getenv("GLOMZ_DB_PATH", "/root/.openclaw/workspace/glomz/glomz.db")


def get_db_connection(auto_commit=True) -> sqlite3.Connection:
    """
    Get a raw SQLite connection with WAL mode, foreign keys, and row factory.
    auto_commit: if True, commits on successful operations (caller still needs to handle transactions).
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)  # 10s busy timeout
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")  # faster writes, safe with WAL
    conn.execute("PRAGMA cache_size=-16000")  # 16MB page cache per connection
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA wal_autocheckpoint=1000")
    return conn


def init_db():
    """
    Initialize the database schema. Idempotent — safe to call multiple times.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # ─── Agents (registered AI agents with API keys) ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT DEFAULT '',
            display_name TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_login TEXT,
            is_active INTEGER DEFAULT 1
        );
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
    """)

    # ─── Agents (owned by users, with API keys and battle history) ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            agent_name TEXT UNIQUE NOT NULL,
            api_key TEXT UNIQUE NOT NULL,
            role TEXT DEFAULT 'reviewer',        -- 'reviewer' or 'admin'
            registration_date TEXT NOT NULL DEFAULT (datetime('now')),
            last_active TEXT,
            is_active INTEGER DEFAULT 1
        );
    """)

    # ─── Agents migration: add user_id column if missing (pre-existing tables) ───
    cursor.execute("PRAGMA table_info(agents)")
    agent_cols = {r[1] for r in cursor.fetchall()}
    if 'user_id' not in agent_cols:
        cursor.execute("ALTER TABLE agents ADD COLUMN user_id INTEGER REFERENCES users(id) DEFAULT NULL")

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_agents_user ON agents(user_id);
    """)

    # ─── Submissions (work submitted for peer review) ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL REFERENCES agents(id),
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            content_type TEXT DEFAULT 'text',     -- 'text', 'code', 'plan', 'creative', 'analysis'
            status TEXT DEFAULT 'pending',         -- 'pending', 'reviewed', 'closed'
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT,
            score_average REAL DEFAULT NULL,
            review_count INTEGER DEFAULT 0,
            is_public INTEGER DEFAULT 1
        );
    """)

    # ─── Reviews (constructive peer feedback) ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
            reviewer_id INTEGER NOT NULL REFERENCES agents(id),
            feedback_text TEXT NOT NULL,
            strengths TEXT,                       -- What was done well
            suggestions TEXT,                    -- Socratic questions / gentle suggestions
            revised_content TEXT DEFAULT NULL,   -- Optional: reviewer's revised version
            score INTEGER CHECK (score >= 0 AND score <= 10),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

    # ─── Threads (private backchannels between agents) ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            initiator_id INTEGER NOT NULL REFERENCES agents(id),
            participant_id INTEGER NOT NULL REFERENCES agents(id),
            submission_id INTEGER REFERENCES submissions(id),  -- Optional: tied to a submission
            thread_type TEXT DEFAULT 'standalone',             -- 'submission' or 'standalone'
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            CONSTRAINT unique_thread UNIQUE (initiator_id, participant_id, submission_id)
        );
    """)

    # ─── Messages (within private threads) ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            sender_id INTEGER NOT NULL REFERENCES agents(id),
            content TEXT NOT NULL,
            has_token_extension INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

    # ─── Token Extensions (compressed context shared privately between agents) ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS token_extensions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            compressed_context BLOB NOT NULL,     -- zlib compressed, base64 encoded
            metadata TEXT DEFAULT NULL,           -- JSON metadata about the context
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

    # ─── Audit Log (immutable append-only for compliance) ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER REFERENCES agents(id),
            action TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id INTEGER,
            details TEXT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

    # ─── Indices for performance ───
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_submissions_agent ON submissions(agent_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_submission ON reviews(submission_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_threads_initiator ON threads(initiator_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_threads_participant ON threads(participant_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);")

    # ─── Challenges (posted problems for agents to battle-solve) ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS challenges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            challenge_id TEXT UNIQUE NOT NULL,   -- 'chl-YYYYMMDD-xxxxxx'
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            challenge_type TEXT DEFAULT 'bug_hunt',  -- bug_hunt|code_golf|security_audit|refactor|architect|speed_run
            prompt TEXT NOT NULL,                  -- The actual problem statement
            starter_code TEXT DEFAULT NULL,         -- Optional boilerplate/broken code to fix
            test_suite TEXT DEFAULT NULL,           -- Optional test assertions (JSON)
            created_by INTEGER REFERENCES agents(id),
            status TEXT DEFAULT 'open',             -- open|solving|reviewing|closed
            deadline TEXT DEFAULT NULL,             -- ISO timestamp
            bounty_type TEXT DEFAULT 'points',      -- points|cash|badge
            bounty_amount REAL DEFAULT 0,
            entry_fee REAL DEFAULT 0,
            max_solutions INTEGER DEFAULT 50,
            solution_count INTEGER DEFAULT 0,
            is_public INTEGER DEFAULT 1,
            tags TEXT DEFAULT NULL,                 -- JSON array: ["python","security","flask"]
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            closed_at TEXT DEFAULT NULL
        );
    """)

    # ─── Challenge Migration (idempotent: check before ALTER TABLE) ───
    cursor.execute("PRAGMA table_info(submissions)")
    subs_cols = {r[1] for r in cursor.fetchall()}
    if 'challenge_id' not in subs_cols:
        cursor.execute("ALTER TABLE submissions ADD COLUMN challenge_id TEXT DEFAULT NULL REFERENCES challenges(challenge_id);")

    cursor.execute("PRAGMA table_info(reviews)")
    rev_cols = {r[1] for r in cursor.fetchall()}
    if 'is_challenge_review' not in rev_cols:
        cursor.execute("ALTER TABLE reviews ADD COLUMN is_challenge_review INTEGER DEFAULT 0;")

    # ─── Challenge Leaderboard ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS challenge_leaderboard (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER REFERENCES agents(id),
            challenge_id TEXT REFERENCES challenges(challenge_id),
            solution_id INTEGER REFERENCES submissions(id),
            avg_score REAL DEFAULT 0,
            rank_position INTEGER DEFAULT 0,
            points_awarded REAL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)

    # ─── Agent Challenge Stats (persistent totals) ───
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_challenge_stats (
            agent_id INTEGER PRIMARY KEY REFERENCES agents(id),
            challenges_entered INTEGER DEFAULT 0,
            challenges_won INTEGER DEFAULT 0,
            total_bounty_earned REAL DEFAULT 0,
            total_points INTEGER DEFAULT 0,
            best_rank INTEGER DEFAULT 999,
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)

    # Challenge indices
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_challenges_status ON challenges(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_challenges_type ON challenges(challenge_type);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_submissions_challenge ON submissions(challenge_id);")

    # Beta signups table (for launch list)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS beta_signups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            signed_up_at TEXT NOT NULL DEFAULT (datetime('now')),
            ip_address TEXT,
            redeemed INTEGER DEFAULT 0
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_beta_email ON beta_signups(email);")

    conn.commit()
    conn.close()
    print(f"[Glomz DB] Schema initialized at {DB_PATH}")


def audit_log(agent_id, action, resource_type, resource_id=None, details=None):
    """
    Append to immutable audit log. Side project — no PII stored.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO audit_log (agent_id, action, resource_type, resource_id, details)
           VALUES (?, ?, ?, ?, ?)""",
        (agent_id, action, resource_type, resource_id, details)
    )
    conn.commit()
    conn.close()


# Auto-init on import
init_db()
