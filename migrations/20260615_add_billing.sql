-- Glomz Billing Migration - 2026-06-15
-- Adds Stripe support, tiers, subscriptions, and billing events

-- SQLite does not support IF NOT EXISTS on ADD COLUMN, so check first
PRAGMA table_info(agents);
-- The following will be run manually or with Python wrapper to avoid errors if columns exist

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stripe_subscription_id TEXT UNIQUE,
    agent_id INTEGER REFERENCES agents(id),
    tier TEXT NOT NULL,
    status TEXT NOT NULL,
    current_period_start DATETIME,
    current_period_end DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS billing_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stripe_event_id TEXT UNIQUE NOT NULL,
    event_type TEXT NOT NULL,
    raw_data TEXT NOT NULL,
    processed BOOLEAN DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_agent ON subscriptions(agent_id);
CREATE INDEX IF NOT EXISTS idx_billing_events_stripe_id ON billing_events(stripe_event_id);

-- Update existing agents to default tier
UPDATE agents SET tier = 'free' WHERE tier IS NULL;