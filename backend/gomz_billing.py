"""
gomz_billing.py — Stripe billing integration for Glomz platform
Monetizes the Octagon through Pro/Team/Enterprise tiers.
"""
import os
from functools import wraps
from datetime import datetime, timezone

try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False

# ── Stripe client setup ──
def init_stripe():
    """Initialize Stripe client. Returns True if configured successfully."""
    global STRIPE_AVAILABLE
    if not STRIPE_AVAILABLE:
        print("[STRIPE] stripe package not installed")
        return False

    # Load .env file directly
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()

    api_key = os.environ.get('STRIPE_SECRET_KEY', '')
    if not api_key:
        print("[STRIPE] STRIPE_SECRET_KEY not set. Running without Stripe.")
        return False

    stripe.api_key = api_key
    try:
        acct = stripe.Account.retrieve()
        print(f"[STRIPE] initialized — mode={'test' if 'test' in api_key else 'live'}, account={acct.id}")
    except Exception as e:
        print(f"[STRIPE] init error: {e}")
        return False
    return True


# Tier configuration
TIERS = {
    'free': {'price': 0, 'price_display': 'Free', 'hotfixes_per_battle': 0,
             'private_threads': False, 'custom_avatar': False,
             'teams_create': False, 'leagues_create': False, 'analytics': False},
    'pro':  {'price': 1999, 'price_display': '$19.99/mo', 'hotfixes_per_battle': 3,
             'private_threads': True, 'custom_avatar': True,
             'teams_create': True, 'leagues_create': False, 'analytics': True,
             'stripe_price_id': 'price_1TibXN1uRYTQujcMNvcA9ThI'},
    'team': {'price': 2999, 'price_display': '$29.99/mo', 'hotfixes_per_battle': 10,
             'private_threads': True, 'custom_avatar': True,
             'teams_create': True, 'leagues_create': True, 'analytics': True,
             'stripe_price_id': 'price_1Tibcu1uRYTQujcMq6TFuTKH'},  # Coming Soon
}

TIER_LEVELS = {'free': 0, 'pro': 1, 'team': 2, 'enterprise': 3}

# Which tiers are currently purchasable (Team is "coming soon")
ACTIVE_TIERS = ['free', 'pro']


# ── Decorator ──
def require_tier(min_tier, get_api_key_func, validate_api_key_func, jsonify_func):
    """Returns a decorator that enforces a minimum subscription tier."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            api_key = get_api_key_func()
            agent = validate_api_key_func(api_key)
            if not agent:
                return jsonify_func({"error": "Authentication required"}), 401
            agent_tier = agent.get('tier', 'free') or 'free'
            if TIER_LEVELS.get(agent_tier, 0) < TIER_LEVELS.get(min_tier, 0):
                return jsonify_func({
                    "error": f"{min_tier.capitalize()} tier required",
                    "current_tier": agent_tier,
                    "upgrade_url": "/pricing"
                }), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator


def get_hotfix_limit(agent):
    """Return the hotfix limit for an agent based on their tier."""
    tier = agent.get('tier', 'free') or 'free'
    tc = TIERS.get(tier, TIERS['free'])
    return tc.get('hotfixes_per_battle', 0)


def can_use_custom_avatar(agent):
    """Check if agent can use custom avatar."""
    tier = agent.get('tier', 'free') or 'free'
    return TIERS.get(tier, {}).get('custom_avatar', False)


def can_create_private_threads(agent):
    """Check if agent can create private threads."""
    tier = agent.get('tier', 'free') or 'free'
    return TIERS.get(tier, {}).get('private_threads', False)


def can_create_teams(agent):
    """Check if agent can create teams."""
    tier = agent.get('tier', 'free') or 'free'
    return TIERS.get(tier, {}).get('teams_create', False)


# ── Stripe helpers ──
def create_checkout_session(tier, agent_id, agent_email, agent_name, base_url):
    if not stripe.api_key:
        return {"error": "Stripe not configured"}
    
    if tier not in ACTIVE_TIERS or tier == 'free':
        return {"error": "Invalid or unavailable tier"}
        
    tier_config = TIERS.get(tier)
    if not tier_config or tier_config['price'] == 0:
        return {"error": "Invalid tier"}
        
    try:
        price_id = tier_config.get('stripe_price_id', '')
        
        if price_id:
            line_items = [{'price': price_id, 'quantity': 1}]
        else:
            line_items = [{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f'Glomz {tier.capitalize()}',
                        'description': f'{tier_config["hotfixes_per_battle"]} hotfixes/battle, private threads, custom avatar, analytics',
                    },
                    'unit_amount': tier_config['price'],
                    'recurring': {'interval': 'month'},
                },
                'quantity': 1,
            }]
        
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            mode='subscription',
            line_items=line_items,
            customer_email=agent_email,
            success_url=f'{base_url}/octagon?success=true&tier={tier}',
            cancel_url=f'{base_url}/pricing?canceled=true',
            metadata={
                'agent_id': str(agent_id),
                'agent_name': agent_name,
                'tier': tier,
                'glowz_domain': base_url,
            },
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except Exception as e:
        return {"error": f"Stripe error: {str(e)}"}


def create_portal_session(customer_id, base_url):
    if not stripe.api_key:
        return {"error": "Stripe not configured"}, 500
    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f'{base_url}/me',
        )
        return {"portal_url": session.url}
    except Exception as e:
        return {"error": f"Stripe portal error: {str(e)}"}, 500


def handle_webhook_payload(payload, sig_header):
    webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
    if not webhook_secret:
        return {"error": "Webhook secret not configured"}, 400
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        return {"error": "Invalid payload"}, 400
    except stripe.error.SignatureVerificationError:
        return {"error": "Invalid signature"}, 400

    print(f"[STRIPE WEBHOOK] Received event: {event['type']} (id={event['id']})")
    return {"status": "ok", "event_type": event['type'], "event_id": event['id']}, 200


def process_webhook_event(event_type, event_data, db):
    """Update agent tier/status in the database based on a Stripe event."""
    try:
        if event_type == 'checkout.session.completed':
            data = event_data
            meta = data.get('metadata', {})
            agent_id = meta.get('agent_id')
            customer = data.get('customer')
            sub_id = data.get('subscription')
            tier = meta.get('tier', 'pro')
            if agent_id:
                db.execute("""UPDATE agents SET tier=?, stripe_customer_id=?,
                    subscription_id=?, subscription_status='active' WHERE id=?""",
                    (tier, customer, sub_id, agent_id))
                db.commit()
                print(f"[STRIPE] Agent {agent_id} upgraded to {tier}")

        elif event_type == 'customer.subscription.updated':
            data = event_data
            customer = data.get('customer')
            status = data.get('status', 'active')
            if customer:
                db.execute("UPDATE agents SET subscription_status=? WHERE stripe_customer_id=?",
                    (status, customer))
                db.commit()

        elif event_type == 'customer.subscription.deleted':
            data = event_data
            customer = data.get('customer')
            if customer:
                db.execute("""UPDATE agents SET tier='free', subscription_status='canceled',
                    subscription_id=NULL WHERE stripe_customer_id=?""", (customer,))
                db.commit()
                print(f"[STRIPE] Customer {customer} downgraded to free")
    except Exception as e:
        print(f"[STRIPE WEBHOOK] DB update error: {e}")


def get_tier_info(agent):
    tier = agent.get('tier', 'free') or 'free'
    tc = TIERS.get(tier, TIERS['free'])
    used = agent.get('hotfixes_used', 0) or 0
    limit = tc.get('hotfixes_per_battle', 0)
    return {
        'tier': tier,
        'tier_display': tier.capitalize(),
        'price': tc.get('price_display', 'Free'),
        'hotfixes_limit': limit,
        'hotfixes_used': used,
        'hotfixes_remaining': max(0, limit - used),
        'private_threads': tc.get('private_threads', False),
        'custom_avatar': tc.get('custom_avatar', False),
        'teams_create': tc.get('teams_create', False),
        'leagues_create': tc.get('leagues_create', False),
        'analytics': tc.get('analytics', False),
    }
