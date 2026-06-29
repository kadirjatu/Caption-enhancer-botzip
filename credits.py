"""
Shared credit system — single source of truth for users.json.
Single lock (_users_lock) used by both bot.py and webapp.py
so no race conditions when both access credits simultaneously.
"""
import json, threading, time

USERS_FILE  = 'users.json'
DAILY_BONUS = 3   # free credits every 24h

_users_lock = threading.Lock()

def _load_users():
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def _save_users(data):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _get_credits(user_id):
    with _users_lock:
        return _load_users().get(str(user_id), {}).get('credits', 0)

def _add_credits(user_id, amount):
    with _users_lock:
        users = _load_users()
        uid = str(user_id)
        if uid not in users:
            users[uid] = {'credits': 0}
        users[uid]['credits'] = users[uid].get('credits', 0) + amount
        _save_users(users)
        return users[uid]['credits']

def _deduct_credits(user_id, amount):
    """Deducts credits. Raises ValueError if balance insufficient."""
    with _users_lock:
        users = _load_users()
        uid = str(user_id)
        if uid not in users:
            users[uid] = {'credits': 0}
        current = users[uid].get('credits', 0)
        if current < amount:
            raise ValueError(f'Kam credits hain! Tumhare paas {current} hain, {amount} chahiye.')
        users[uid]['credits'] = current - amount
        _save_users(users)
        return users[uid]['credits']

def _check_credits(user_id, amount):
    """Checks if user has enough credits. Raises ValueError if not. Does NOT deduct."""
    with _users_lock:
        users = _load_users()
        uid = str(user_id)
        current = users.get(uid, {}).get('credits', 0)
        if current < amount:
            raise ValueError(f'Kam credits hain! Tumhare paas {current} hain, {amount} chahiye.')

def _add_history(user_id, job_id, task_type, cost, desc=''):
    """Adds a task to user history (max 3 entries, oldest removed)."""
    with _users_lock:
        users = _load_users()
        uid = str(user_id)
        if uid not in users:
            users[uid] = {'credits': 0}
        history = users[uid].get('history', [])
        history.append({'job_id': job_id, 'type': task_type, 'desc': desc,
                        'cost': cost, 'status': 'processing',
                        'created_at': int(time.time())})
        users[uid]['history'] = history[-3:]
        _save_users(users)

def _update_history_status(user_id, job_id, status):
    with _users_lock:
        users = _load_users()
        uid = str(user_id)
        if uid in users:
            for item in users[uid].get('history', []):
                if item.get('job_id') == job_id:
                    item['status'] = status
                    break
            _save_users(users)

def _check_and_give_daily_bonus(user_id):
    """Returns {given, amount, credits, next_in} dict."""
    with _users_lock:
        users = _load_users()
        uid = str(user_id)
        if uid not in users:
            users[uid] = {'credits': 0}
        now = time.time()
        last_bonus = users[uid].get('last_bonus', 0)
        if now - last_bonus >= 24 * 3600:
            users[uid]['credits'] = users[uid].get('credits', 0) + DAILY_BONUS
            users[uid]['last_bonus'] = now
            _save_users(users)
            return {'given': True, 'amount': DAILY_BONUS,
                    'credits': users[uid]['credits'], 'next_in': 24 * 3600}
        next_in = int(24 * 3600 - (now - last_bonus))
        return {'given': False, 'amount': 0,
                'credits': users[uid].get('credits', 0), 'next_in': next_in}

# ── Bot-friendly wrappers (no underscore, no ValueError) ─────────────────────
_SIGNUP_CREDITS   = 10
_REFERRAL_CREDITS = 20

def get_credits(user_id):
    return _get_credits(user_id)

def add_credits(user_id, amount):
    _add_credits(user_id, amount)

def deduct_credits(user_id, amount):
    """Deduct without raising — clamps to 0 if insufficient (bot-style)."""
    with _users_lock:
        users = _load_users()
        uid = str(user_id)
        if uid not in users:
            users[uid] = {'credits': 0}
        users[uid]['credits'] = max(0, users[uid].get('credits', 0) - amount)
        _save_users(users)

def register_user_credits(user_id, referrer_id=None):
    """Give signup credits once per user (checked via 'registered' flag in users.json).
    Returns True if new user, False if already registered."""
    uid = str(user_id)
    with _users_lock:
        users = _load_users()
        if users.get(uid, {}).get('registered'):
            return False
        if uid not in users:
            users[uid] = {'credits': 0}
        users[uid]['registered'] = True
        users[uid]['credits'] = users[uid].get('credits', 0) + _SIGNUP_CREDITS
        if referrer_id and str(referrer_id) != uid:
            rid = str(referrer_id)
            if rid not in users:
                users[rid] = {'credits': 0}
            users[rid]['credits'] = users[rid].get('credits', 0) + _REFERRAL_CREDITS
            users[rid]['referral_count'] = users[rid].get('referral_count', 0) + 1
        _save_users(users)
    return True
