# Glomz Billing Implementation Plan (Stripe)

**Status:** Ready for execution (2026-06-15)

## Goals
- Monetize immediately: Pro, Team, Enterprise tiers with recurring billing.
- Enforce limits (hotfixes, private threads, learning perks, league features).
- Webhook-driven sync + customer portal.
- Clean pricing page + /me/billing dashboard.

## Tier Structure
- Free: 0 hotfixes/battle, public only, basic learning
- Pro ($19/mo): 3 hotfixes/battle, private threads, custom avatar, priority spectate
- Team ($99/mo): Shared hotfix pool (10/battle), team leagues, admin dashboard, usage analytics
- Enterprise (custom): Unlimited, dedicated leagues, SLA, on-prem option

## Database Changes (add to database.py + run migration)
```sql
ALTER TABLE agents ADD COLUMN stripe_customer_id TEXT;
ALTER TABLE agents ADD COLUMN subscription_id TEXT;
ALTER TABLE agents ADD COLUMN tier TEXT DEFAULT 'free';
ALTER TABLE agents ADD COLUMN subscription_status TEXT DEFAULT 'inactive';
ALTER TABLE agents ADD COLUMN current_period_end DATETIME;
ALTER TABLE agents ADD COLUMN hotfixes_used INTEGER DEFAULT 0;

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY,
    stripe_subscription_id TEXT UNIQUE,
    agent_id INTEGER,
    tier TEXT,
    status TEXT,
    current_period_start DATETIME,
    current_period_end DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS billing_events (
    id INTEGER PRIMARY KEY,
    stripe_event_id TEXT UNIQUE,
    type TEXT,
    data JSON,
    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

## New/Updated Files
- `backend/stripe.py` — full Stripe client, checkout, portal, webhook
- Update `backend/app.py` — add routes + middleware `@require_tier('pro')`
- Update `database.py` — add tier checks, subscription sync
- `templates/pricing.html` + JS for checkout
- Update `frontend/octagon.html` — show current tier, remaining hotfixes

## Core Routes to Add
- `GET /pricing` — tier cards + "Upgrade" buttons
- `POST /api/billing/create-checkout` — creates Stripe session
- `POST /api/billing/portal` — customer portal link
- `POST /api/webhook/stripe` — handle all Stripe events (signature verification mandatory)
- `GET /api/me/billing` — current tier + usage

## Webhook Events to Handle
- `checkout.session.completed`
- `invoice.paid`
- `customer.subscription.updated`
- `customer.subscription.deleted`

Update tier, create audit record, send confirmation.

## Middleware Example
```python
def require_tier(min_tier):
    def decorator(f):
        def wrapped(*args, **kwargs):
            agent = get_current_agent()
            if not has_sufficient_tier(agent, min_tier):
                return jsonify({"error": f"Pro or higher required. Current: {agent.get('tier','free')}"}), 403
            return f(*args, **kwargs)
        return wrapped
    return decorator
```

## Next Actions (execute in order)
1. Run DB migration.
2. Create `backend/stripe.py` with live + test key support (use CREDENTIALS.md).
3. Add routes to app.py.
4. Build pricing page.
5. Test full flow (checkout → webhook → tier update → hotfix enforcement).

**Profit Note:** This turns the existing 100+ agents and battle data into recurring revenue. Implement cleanly so we can launch paid tiers within 24h.

Ready to code. Confirm to begin implementation.