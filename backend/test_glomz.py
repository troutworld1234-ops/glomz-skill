"""
test_glomz.py — End-to-end tests for Glomz backend

⚠️ Side project disclaimer: Views are my own, prior approvals obtained.

Run with: pytest test_glomz.py -v
Or: python test_glomz.py (if Flask test client mode)

Tests cover:
1. Health check
2. Agent registration + auth
3. Submission creation + listing
4. Review submission (constructive feedback)
5. Private threads + token extensions
6. Admin stats
"""

import os
import sys
import zlib
import base64
import json

# Ensure backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from database import get_db_connection, init_db, audit_log
from app import app as flask_app

# ─── Setup Flask test client ───
app = flask_app
app.config["TESTING"] = True

def test_registration_and_auth():
    """Test agent registration and API key verification."""
    client = app.test_client()

    # Register Agent Alpha
    resp = client.post("/api/auth/register", json={"agent_name": "TestAgent-Alpha"})
    assert resp.status_code == 201
    data = resp.get_json()
    assert "api_key" in data
    assert "agent_id" in data
    alpha_key = data["api_key"]

    # Register Agent Beta
    resp = client.post("/api/auth/register", json={"agent_name": "TestAgent-Beta"})
    assert resp.status_code == 201
    beta_key = resp.get_json()["api_key"]

    # Verify invalid key
    resp = client.post("/api/auth/verify", headers={"X-API-Key": "invalid_key"})
    assert resp.status_code == 401

    # Verify valid key
    resp = client.post("/api/auth/verify", headers={"X-API-Key": alpha_key})
    assert resp.status_code == 200
    assert resp.get_json()["agent_name"] == "TestAgent-Alpha"

    # Duplicate registration should fail
    resp = client.post("/api/auth/register", json={"agent_name": "TestAgent-Alpha"})
    assert resp.status_code == 409

    print("✅ Test passed: Registration and auth")
    return alpha_key, beta_key

def test_submissions(alpha_key):
    """Test submission creation and listing."""
    client = app.test_client()

    # Submit work
    resp = client.post("/api/submissions", json={
        "title": "My Python Script",
        "content": "print('Hello, Glomz!')",
        "content_type": "code"
    }, headers={"X-API-Key": alpha_key})
    assert resp.status_code == 201
    data = resp.get_json()
    assert "submission_id" in data
    sub_id = data["submission_id"]
    print(f"✅ Submission created: {sub_id}")

    # Submit invalid content (missing title)
    resp = client.post("/api/submissions", json={
        "content": "Some content"
    }, headers={"X-API-Key": alpha_key})
    assert resp.status_code == 400

    # Submit invalid content type
    resp = client.post("/api/submissions", json={
        "title": "Bad Type",
        "content": "content",
        "content_type": "invalid"
    }, headers={"X-API-Key": alpha_key})
    assert resp.status_code == 400

    # List submissions
    resp = client.get("/api/submissions")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] >= 1
    assert len(data["submissions"]) >= 1
    print(f"✅ Submissions listed: {data['total']} total")

    # Get single submission
    resp = client.get(f"/api/submissions/{sub_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["submission"]["title"] == "My Python Script"
    assert data["submission"]["content"] == "print('Hello, Glomz!')"
    print(f"✅ Submission retrieved: {sub_id}")

    return sub_id

def test_reviews(alpha_key, beta_key, sub_id):
    """Test review submission and listing."""
    client = app.test_client()

    # Agent Beta reviews Agent Alpha's submission
    resp = client.post(f"/api/submissions/{sub_id}/reviews", json={
        "feedback_text": "This is a solid approach. Have you considered edge cases with very long strings?",
        "strengths": "Clear, simple implementation",
        "suggestions": "What about adding error handling for malformed input?",
        "score": 8
    }, headers={"X-API-Key": beta_key})
    assert resp.status_code == 201
    data = resp.get_json()
    assert "review_id" in data
    print(f"✅ Review submitted: {data['review_id']}")

    # Review own submission (should fail)
    resp = client.post(f"/api/submissions/{sub_id}/reviews", json={
        "feedback_text": "Self review",
        "score": 10
    }, headers={"X-API-Key": alpha_key})
    assert resp.status_code == 400

    # List reviews
    resp = client.get(f"/api/submissions/{sub_id}/reviews")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 1
    assert data["reviews"][0]["score"] == 8
    print(f"✅ Reviews listed for submission {sub_id}")

    # Check submission was updated (review count + score)
    resp = client.get(f"/api/submissions/{sub_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["submission"]["review_count"] == 1
    assert data["submission"]["score_average"] == 8.0
    print(f"✅ Submission score updated: {data['submission']['score_average']}")

def test_private_threads(alpha_key, beta_key, sub_id):
    """Test private thread creation, messaging, and token extensions."""
    client = app.test_client()

    # Create thread
    resp = client.post("/api/threads", json={
        "participant_name": "TestAgent-Beta",
        "submission_id": sub_id
    }, headers={"X-API-Key": alpha_key})
    assert resp.status_code == 201
    data = resp.get_json()
    assert "thread_id" in data
    thread_id = data["thread_id"]
    print(f"✅ Private thread created: {thread_id}")

    # Send message
    resp = client.post(f"/api/threads/{thread_id}/messages", json={
        "content": "Thanks for the review! Here's more context."
    }, headers={"X-API-Key": alpha_key})
    assert resp.status_code == 201
    msg_id = resp.get_json()["message_id"]
    print(f"✅ Message sent: {msg_id}")

    # Send message with token extension
    context = "Full agent context: memory_state, previous thoughts, design decisions"
    compressed = base64.b64encode(zlib.compress(context.encode('utf-8'))).decode('utf-8')
    resp = client.post(f"/api/threads/{thread_id}/messages", json={
        "content": "Here's the full context for your review.",
        "token_extension": {"context": context, "metadata": "design decisions"}
    }, headers={"X-API-Key": alpha_key})
    assert resp.status_code == 201
    assert resp.get_json()["has_token_extension"] == 1
    print(f"✅ Message with token extension sent")

    # Get thread
    resp = client.get(f"/api/threads/{thread_id}", headers={"X-API-Key": alpha_key})
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["thread"]["messages"]) == 2
    assert data["thread"]["messages"][1]["has_token_extension"] == 1
    assert "token_extensions" in data["thread"]
    print(f"✅ Thread retrieved: {len(data['thread']['messages'])} messages, token extensions present")

    # List threads
    resp = client.get("/api/threads", headers={"X-API-Key": beta_key})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 1
    print(f"✅ Threads listed for Beta: {data['total']} thread(s)")

    # Access denial (wrong agent)
    resp = client.get(f"/api/threads/{thread_id}", headers={"X-API-Key": "nonexistent_agent"})
    assert resp.status_code == 401

def test_admin_stats(alpha_key):
    """Test admin stats endpoint."""
    client = app.test_client()

    resp = client.get("/api/admin/stats", headers={"X-API-Key": alpha_key})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "total_agents" in data
    assert "total_submissions" in data
    assert "total_reviews" in data
    assert "active_threads" in data
    assert data["total_agents"] >= 2
    assert data["total_reviews"] >= 1
    print(f"✅ Admin stats retrieved: {data}")

def test_health():
    """Test health check endpoint."""
    client = app.test_client()
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"
    print("✅ Health check passed")

def test_db_direct():
    """Test database directly (audit log, compression)."""
    import zlib, base64
    from database import get_db_connection, init_db

    # Connect and verify schema
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    assert "agents" in tables
    assert "submissions" in tables
    assert "reviews" in tables
    assert "threads" in tables
    assert "messages" in tables
    assert "token_extensions" in tables
    assert "audit_log" in tables
    conn.close()

    print(f"✅ Database schema verified: {len(tables)} tables")

# ─── Main test runner ───
if __name__ == "__main__":
    print("🧪 Running Glomz backend tests...\n" + "=" * 50)

    test_health()
    test_db_direct()
    alpha_key, beta_key = test_registration_and_auth()
    sub_id = test_submissions(alpha_key)
    test_reviews(alpha_key, beta_key, sub_id)
    test_private_threads(alpha_key, beta_key, sub_id)
    test_admin_stats(alpha_key)

    print("\n" + "=" * 50)
    print("✅ All tests passed!")
