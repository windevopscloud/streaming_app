import asyncio
import html
import os
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

from . import config, messaging, payments, security as sec
from .qr import qr_svg
from .db import db
from .security import ApiError

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")
TOPIC_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
GENERIC = {"ok": True, "message": "If the details are valid, a code or email was sent."}


# ---------------------------------------------------------------- helpers
def norm_email(v):
    v = str(v or "").strip().lower()
    if len(v) > 254 or not EMAIL_RE.match(v):
        raise ApiError(400, "invalid email")
    return v


def channels():
    return ("email", "sms") if config.SMS_ENABLED else ("email",)


def norm_phone(v):
    v = re.sub(r"[\s\-()]", "", str(v or ""))
    if not PHONE_RE.match(v):
        raise ApiError(400, "phone must be E.164, e.g. +919876543210")
    return v


def norm_topic(v):
    v = str(v or "").strip().lower()
    if not TOPIC_RE.match(v):
        raise ApiError(400, "topic must be 1-40 chars of a-z 0-9 _ -")
    return v


def get_user(email):
    return db.one("SELECT * FROM users WHERE email=?", (email,))


def api(fn):
    async def handler(request):
        try:
            try:
                data = await request.json()
            except Exception:
                data = {}
            if not isinstance(data, dict):
                raise ApiError(400, "invalid json")
            return JSONResponse(await run_in_threadpool(fn, data, request))
        except ApiError as e:
            return JSONResponse({"error": e.msg}, status_code=e.status)
    return handler


# ---------------------------------------------------------------- registration + verification
def _send_user_code(user, channel):
    if channel == "email":
        sec.send_code(user, "email", lambda c: messaging.send_email(
            user["email"], "Your StreamNotify code",
            f"Your StreamNotify code is {c}. It expires in {config.OTP_TTL // 60} minutes."))
    else:
        sec.send_code(user, "sms", lambda c: messaging.send_sms(
            user["phone"], f"Your StreamNotify code is {c}. Valid {config.OTP_TTL // 60} min."))


def register(data, req):
    email = norm_email(data.get("email"))
    phone = norm_phone(data.get("phone")) if config.SMS_ENABLED else ""  # phone column stays NOT NULL: "" = none
    user = get_user(email)
    if user and _fully_verified(user):
        return GENERIC  # never reveal that the account exists
    if user:
        if config.SMS_ENABLED:
            db.x("UPDATE users SET phone=?, phone_verified=0 WHERE id=?", (phone, user["id"]))
            user = get_user(email)
    else:
        uid = uuid.uuid4().hex
        db.x("INSERT INTO users (id, email, phone, created) VALUES (?,?,?,?)",
             (uid, email, phone, sec.now()))
        user = get_user(email)
    _send_user_code(user, "email")
    if config.SMS_ENABLED:
        _send_user_code(user, "sms")
    return GENERIC


def _fully_verified(user):
    return bool(user["email_verified"]) and (bool(user["phone_verified"]) or not config.SMS_ENABLED)


def verify(data, req):
    email, channel = norm_email(data.get("email")), data.get("channel")
    if channel not in channels():
        raise ApiError(400, "channel must be " + " or ".join(channels()))
    user = get_user(email)
    if not user or not sec.check_code(user, channel, data.get("code")):
        raise ApiError(400, "invalid or expired code")
    col = "email_verified" if channel == "email" else "phone_verified"
    db.x(f"UPDATE users SET {col}=1 WHERE id=?", (user["id"],))
    user = get_user(email)
    return {"ok": True, "email_verified": bool(user["email_verified"]),
            "phone_verified": bool(user["phone_verified"]), "sms": config.SMS_ENABLED}


# ---------------------------------------------------------------- login (factor 1: OTP, factor 2: TOTP)
def login(data, req):
    email, channel = norm_email(data.get("email")), data.get("channel")
    if channel not in channels():
        raise ApiError(400, "channel must be " + " or ".join(channels()))
    user = get_user(email)
    verified = user and _fully_verified(user)
    if verified:
        try:
            _send_user_code(user, channel)
        except ApiError:
            pass  # throttled: answer identically so existence is not revealed
    return GENERIC


def login_verify(data, req):
    email, channel = norm_email(data.get("email")), data.get("channel")
    user = get_user(email)
    if channel not in channels() or not user or not _fully_verified(user) \
            or not sec.check_code(user, channel, data.get("code")):
        raise ApiError(400, "invalid or expired code")
    if user["totp_enabled"]:
        return {"stage": "2fa_required", "token": sec.create_session(user["id"], "partial")}
    if config.MFA_REQUIRED:      # first login: the only thing this session can do is enrol an authenticator
        return {"stage": "setup_required", "token": sec.create_session(user["id"], "setup")}
    return {"stage": "ok", "token": sec.create_session(user["id"], "full")}


def tfa_verify(data, req):
    s = sec.get_session(req, "partial")
    user = db.one("SELECT * FROM users WHERE id=?", (s["user_id"],))
    row = db.one("SELECT attempts FROM sessions WHERE token_hash=?", (s["token_hash"],))
    if row["attempts"] >= config.TFA_MAX_ATTEMPTS:
        db.x("DELETE FROM sessions WHERE token_hash=?", (s["token_hash"],))
        raise ApiError(401, "too many attempts, log in again")
    db.x("UPDATE sessions SET attempts=attempts+1 WHERE token_hash=?", (s["token_hash"],))
    ok = False
    if data.get("recovery_code"):
        ok = sec.use_recovery_code(user["id"], data["recovery_code"])
    elif user["totp_secret"]:
        step = sec.totp_match(user["totp_secret"], data.get("code"), user["totp_last_step"])
        if step:  # atomic replay guard: a step can be used once
            ok = db.x("UPDATE users SET totp_last_step=? WHERE id=? AND totp_last_step<?",
                      (step, user["id"], step)) == 1
    if not ok:
        raise ApiError(400, "invalid code")
    db.x("DELETE FROM sessions WHERE token_hash=?", (s["token_hash"],))
    return {"stage": "ok", "token": sec.create_session(user["id"], "full")}


def _close_history(email, topic):
    db.x("UPDATE sub_history SET unsubscribed=? WHERE email=? AND topic=? AND unsubscribed=0",
         (sec.now(), email, topic))


def _open_history(email, topic, price_cents):
    if not db.one("SELECT id FROM sub_history WHERE email=? AND topic=? AND unsubscribed=0", (email, topic)):
        db.x("INSERT INTO sub_history (id, email, topic, price_cents, subscribed) VALUES (?,?,?,?,?)",
             (uuid.uuid4().hex, email, topic, price_cents, sec.now()))


def _active_subs(email):
    return db.q("SELECT topic, expires FROM subscriptions WHERE email=? AND status='active' "
                "AND (expires=0 OR expires>?) ORDER BY topic", (email, sec.now()))


def me(data, req):
    s = sec.get_session(req, "full")
    u = db.one("SELECT * FROM users WHERE id=?", (s["user_id"],))
    rec = db.one("SELECT COUNT(*) AS n FROM recovery_codes WHERE user_id=? AND used=0", (u["id"],))["n"]
    return {"email": u["email"], "phone": u["phone"], "totp_enabled": bool(u["totp_enabled"]),
            "recovery_remaining": rec,
            "subscriptions": [r["topic"] for r in _active_subs(u["email"])],
            "subscription_details": [{"topic": r["topic"], "expires": r["expires"]} for r in _active_subs(u["email"])]}


def history(data, req):
    """Subscribe / unsubscribe activity of the last 30 days."""
    s = sec.get_session(req, "full")
    since = sec.now() - 30 * 86400
    rows = db.q("SELECT h.topic, h.price_cents, h.subscribed, h.unsubscribed, p.title FROM sub_history h "
                "LEFT JOIN plans p ON p.topic=h.topic WHERE h.email=? AND (h.subscribed>? OR h.unsubscribed>?) "
                "ORDER BY h.subscribed DESC", (s["email"], since, since))
    return {"history": rows, "days": 30}


def feed(data, req):
    """Latest content from the topics the user is subscribed to."""
    s = sec.get_session(req, "full")
    topics = sorted(_allowed_topics(s["email"]))
    if not topics:
        return {"events": []}
    ph = ",".join("?" * len(topics))
    rows = db.q(f"SELECT seq, topic, message, created FROM events WHERE topic IN ({ph}) "
                f"ORDER BY seq DESC LIMIT 30", tuple(topics))
    return {"events": rows}


def logout(data, req):
    s = sec.get_session(req, "full")
    db.x("DELETE FROM sessions WHERE token_hash=?", (s["token_hash"],))
    return {"ok": True}


# ---------------------------------------------------------------- 2FA management
def tfa_setup(data, req):
    s = sec.get_session(req, ("full", "setup"))
    u = db.one("SELECT * FROM users WHERE id=?", (s["user_id"],))
    if u["totp_enabled"]:
        raise ApiError(409, "2FA already enabled")
    secret = sec.new_totp_secret()
    db.x("UPDATE users SET totp_secret=? WHERE id=?", (secret, u["id"]))
    uri = sec.otpauth_uri(u["email"], secret)
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": qr_svg(uri)}


def tfa_enable(data, req):
    s = sec.get_session(req, ("full", "setup"))
    u = db.one("SELECT * FROM users WHERE id=?", (s["user_id"],))
    if u["totp_enabled"] or not u["totp_secret"]:
        raise ApiError(409, "start setup first")
    step = sec.totp_match(u["totp_secret"], data.get("code"), 0)
    if not step:
        raise ApiError(400, "invalid code")
    db.x("UPDATE users SET totp_enabled=1, totp_last_step=? WHERE id=?", (step, u["id"]))
    out = {"ok": True, "recovery_codes": sec.make_recovery_codes(u["id"])}
    if s["stage"] == "setup":    # enrolment done: swap the setup session for a full one
        db.x("DELETE FROM sessions WHERE token_hash=?", (s["token_hash"],))
        out["token"] = sec.create_session(u["id"], "full")
    return out


def tfa_disable(data, req):
    s = sec.get_session(req, "full")
    u = db.one("SELECT * FROM users WHERE id=?", (s["user_id"],))
    step = u["totp_enabled"] and sec.totp_match(u["totp_secret"], data.get("code"), u["totp_last_step"])
    if not step:
        raise ApiError(400, "invalid code")
    db.x("UPDATE users SET totp_enabled=0, totp_secret=NULL, totp_last_step=0 WHERE id=?", (u["id"],))
    db.x("DELETE FROM recovery_codes WHERE user_id=?", (u["id"],))
    return {"ok": True}


def tfa_regenerate(data, req):
    """New set of recovery codes (old ones stop working). Needs a fresh authenticator code."""
    s = sec.get_session(req, "full")
    u = db.one("SELECT * FROM users WHERE id=?", (s["user_id"],))
    step = u["totp_enabled"] and sec.totp_match(u["totp_secret"], data.get("code"), u["totp_last_step"])
    if not step:
        raise ApiError(400, "invalid code")
    if db.x("UPDATE users SET totp_last_step=? WHERE id=? AND totp_last_step<?", (step, u["id"], step)) != 1:
        raise ApiError(400, "invalid code")
    return {"ok": True, "recovery_codes": sec.make_recovery_codes(u["id"])}


# ---------------------------------------------------------------- email subscription (double opt-in)
def _page(title, msg, status=200):
    body = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>{html.escape(title)}</title><body style='font-family:system-ui;max-width:32rem;"
            f"margin:4rem auto;padding:0 1rem'><h1>{html.escape(title)}</h1><p>{html.escape(msg)}</p>")
    return HTMLResponse(body, status_code=status)


def subscribe(data, req):
    email, topic = norm_email(data.get("email")), norm_topic(data.get("topic"))
    plan = db.one("SELECT price_cents FROM plans WHERE topic=?", (topic,))
    if not plan:
        raise ApiError(400, "unknown topic")
    if plan["price_cents"] > 0:
        raise ApiError(400, "this is a paid plan: log in and use checkout")
    t = sec.now()
    sub = db.one("SELECT * FROM subscriptions WHERE email=? AND topic=?", (email, topic))
    if sub and sub["status"] == "active":
        return GENERIC
    if sub:
        if t - sub["last_sent"] < config.CONFIRM_RESEND_GAP:
            return GENERIC
        db.x("UPDATE subscriptions SET status='pending', last_sent=? WHERE id=?", (t, sub["id"]))
        sid = sub["id"]
    else:
        sid = uuid.uuid4().hex
        try:
            db.x("INSERT INTO subscriptions (id, email, topic, status, last_sent, created) "
                 "VALUES (?,?,?,?,?,?)", (sid, email, topic, "pending", t, t))
        except Exception:
            return GENERIC  # concurrent duplicate
    token = sec.sign("c", sid, t + config.CONFIRM_TTL)
    messaging.send_email(
        email, f"Confirm your subscription to '{topic}'",
        f"Confirm your subscription to '{topic}':\n{config.BASE_URL}/confirm?token={token}\n\n"
        f"This link expires in {config.CONFIRM_TTL // 3600} hours. If this wasn't you, ignore this email.")
    return GENERIC


async def confirm(request):
    parts = sec.unsign(request.query_params.get("token"))
    if not parts or len(parts) != 3 or parts[0] != "c":
        return _page("Invalid link", "This confirmation link is not valid.", 400)
    _, sid, exp = parts
    sub = await run_in_threadpool(db.one, "SELECT * FROM subscriptions WHERE id=?", (sid,))
    if not sub:
        return _page("Invalid link", "This confirmation link is not valid.", 400)
    if sub["status"] == "active":
        return _page("Already confirmed", f"You are subscribed to '{sub['topic']}'.")
    if int(exp) < sec.now():
        return _page("Link expired", "Please subscribe again to get a new link.", 410)
    await run_in_threadpool(db.x, "UPDATE subscriptions SET status='active' WHERE id=?", (sid,))
    await run_in_threadpool(_open_history, sub["email"], sub["topic"], 0)
    return _page("Subscribed", f"You will now receive '{sub['topic']}' events.")


async def unsubscribe(request):
    parts = sec.unsign(request.query_params.get("token"))
    if not parts or len(parts) != 2 or parts[0] != "u":
        return _page("Invalid link", "This unsubscribe link is not valid.", 400)
    sub = await run_in_threadpool(db.one, "SELECT * FROM subscriptions WHERE id=?", (parts[1],))
    if sub:
        await run_in_threadpool(db.x, "UPDATE subscriptions SET status='unsubscribed' WHERE id=?", (sub["id"],))
        await run_in_threadpool(_close_history, sub["email"], sub["topic"])
        broker.kick(sub["email"], sub["topic"])
    return _page("Unsubscribed", "You will no longer receive these emails.")


async def unsubscribe_api(request):
    """Logged-in user unsubscribes from a topic (in-page button)."""
    try:
        s = await run_in_threadpool(sec.get_session, request, "full")
        data = await request.json()
        topic = norm_topic(data.get("topic"))
    except ApiError as e:
        return JSONResponse({"error": e.msg}, status_code=e.status)
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    n = await run_in_threadpool(
        db.x, "UPDATE subscriptions SET status='unsubscribed' WHERE email=? AND topic=? AND status<>'unsubscribed'",
        (s["email"], topic))
    await run_in_threadpool(_close_history, s["email"], topic)
    broker.kick(s["email"], topic)
    return JSONResponse({"ok": True, "unsubscribed": n == 1})


# ---------------------------------------------------------------- publish + stream (SSE)
class Broker:
    def __init__(self):
        self.subs = {}

    def add(self, topics, q):
        for t in topics:
            self.subs.setdefault(t, set()).add(q)

    def remove(self, topics, q):
        for t in topics:
            self.subs.get(t, set()).discard(q)

    def kick(self, email, topic):
        """Close open streams of `email` on `topic` (after unsubscribe)."""
        for q in list(self.subs.get(topic, ())):
            if getattr(q, "email", None) == email:
                try:
                    q.put_nowait(None)
                except asyncio.QueueFull:
                    self.subs[topic].discard(q)

    def publish(self, ev):
        for q in list(self.subs.get(ev["topic"], ())):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass  # slow consumer; it can resume with Last-Event-ID


broker = Broker()
_seq_lock, _last_seq = threading.Lock(), [0]


def _store_event(topic, message):
    with _seq_lock:
        seq = max(time.time_ns() // 1000, _last_seq[0] + 1)
        _last_seq[0] = seq
    db.x("INSERT INTO events (seq, topic, message, created) VALUES (?,?,?,?)",
         (seq, topic, message, sec.now()))
    return {"seq": seq, "topic": topic, "message": message}


def _email_subscribers(ev):
    for s in db.q("SELECT id, email FROM subscriptions WHERE topic=? AND status='active' "
                  "AND (expires=0 OR expires>?)", (ev["topic"], sec.now())):
        try:
            messaging.send_email(
                s["email"], f"[{ev['topic']}] {ev['message'][:60]}",
                f"{ev['message']}\n\nUnsubscribe: {config.BASE_URL}/unsubscribe?token={sec.sign('u', s['id'])}")
        except Exception as e:  # one bad address must not stop the rest
            print("email fan-out failed:", e, flush=True)


async def publish(request):
    key = request.headers.get("x-publish-key", "")
    if not config.PUBLISH_KEY or not sec.eq(key, config.PUBLISH_KEY):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        data = await request.json()
        topic, message = norm_topic(data.get("topic")), str(data.get("message") or "").strip()
        if not message or len(message) > 2000:
            raise ApiError(400, "message required (max 2000 chars)")
    except ApiError as e:
        return JSONResponse({"error": e.msg}, status_code=e.status)
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    ev = await run_in_threadpool(_store_event, topic, message)
    broker.publish(ev)
    return JSONResponse({"ok": True, "seq": ev["seq"]},
                        background=BackgroundTask(_email_subscribers, ev))


def _fmt(ev):
    data = ev["message"].replace("\r", "").replace("\n", "\ndata: ")
    return f"id: {ev['seq']}\nevent: {ev['topic']}\ndata: {data}\n\n"


def _allowed_topics(email):
    return {r["topic"] for r in _active_subs(email)}


def _events_since(topics, last):
    ph = ",".join("?" * len(topics))
    return db.q(f"SELECT seq, topic, message FROM events WHERE seq>? AND topic IN ({ph}) "
                f"ORDER BY seq LIMIT 200", (last, *topics))


async def stream(request):
    try:
        s = await run_in_threadpool(sec.get_session, request, "full")
        topics = [norm_topic(t) for t in request.query_params.get("topics", "").split(",") if t]
        if not topics:
            raise ApiError(400, "topics required")
        allowed = await run_in_threadpool(_allowed_topics, s["email"])
        if not set(topics) <= allowed:
            raise ApiError(403, "subscription not confirmed for one or more topics")
    except ApiError as e:
        return JSONResponse({"error": e.msg}, status_code=e.status)
    try:
        last = int(request.headers.get("last-event-id") or request.query_params.get("last_id") or 0)
    except ValueError:
        last = 0
    q = asyncio.Queue(maxsize=200)
    q.email = s["email"]
    broker.add(topics, q)

    async def gen():
        try:
            yield ": connected\nretry: 3000\n\n"
            if last:
                for ev in await run_in_threadpool(_events_since, topics, last):
                    yield _fmt(ev)
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), 15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if ev is None:          # unsubscribed: end the stream
                    yield "event: unsubscribed\ndata: bye\n\n"
                    return
                yield _fmt(ev)
        finally:
            broker.remove(topics, q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- paid plans + TEST checkout
DECLINE = {"invalid_card": "Payment declined: invalid card details",
           "insufficient_balance": "Payment declined: insufficient balance"}


def app_config(data, req):
    return {"sms": config.SMS_ENABLED}


def plans_list(data, req):
    return {"plans": db.q("SELECT topic, title, description, price_cents FROM plans ORDER BY price_cents, topic"),
            "days": config.SUB_DAYS, "payment_mode": config.PAYMENT_MODE}


def test_cards(data, req):
    sec.get_session(req, "full")
    if config.PAYMENT_MODE != "test":
        raise ApiError(404, "not found")
    return {"cards": payments.list_cards()}


def checkout(data, req):
    """Buy one or more plans in a single payment. Free plans in the cart are activated directly (the email is already verified)."""
    s = sec.get_session(req, "full")
    if config.PAYMENT_MODE != "test":
        raise ApiError(503, "payments disabled")
    raw = data.get("topics")
    if raw is None:
        raw = [data.get("topic")]
    if not isinstance(raw, list) or not raw or len(raw) > 20:
        raise ApiError(400, "choose at least one subscription")
    topics = list(dict.fromkeys(norm_topic(t) for t in raw))
    plans = []
    for topic in topics:
        plan = db.one("SELECT * FROM plans WHERE topic=?", (topic,))
        if not plan:
            raise ApiError(404, "unknown plan")
        plans.append(plan)
    amount = sum(p["price_cents"] for p in plans)
    last4 = None
    pid = None
    if amount > 0:
        ok, reason, last4 = payments.charge(data.get("card_number"), data.get("expiry"), data.get("cvv"), amount)
        pid = uuid.uuid4().hex
        db.x("INSERT INTO payments (id, user_id, topic, card_last4, amount_cents, status, reason, created) "
             "VALUES (?,?,?,?,?,?,?,?)",
             (pid, s["user_id"], ",".join(topics), last4, amount, "approved" if ok else "declined", reason, sec.now()))
        if not ok:
            raise ApiError(402, DECLINE[reason])
    expiries = {}
    try:
        t = sec.now()
        for plan in plans:
            topic = plan["topic"]
            sub = db.one("SELECT * FROM subscriptions WHERE email=? AND topic=?", (s["email"], topic))
            days = config.SUB_DAYS * 86400 if plan["price_cents"] > 0 else 0
            if sub:
                live = sub["status"] == "active" and (sub["expires"] == 0 or sub["expires"] > t)
                base = max(t, sub["expires"]) if live else t        # renewing extends, never shortens
                expires = base + days if days else 0
                db.x("UPDATE subscriptions SET status='active', expires=? WHERE id=?", (expires, sub["id"]))
            else:
                expires = t + days if days else 0
                db.x("INSERT INTO subscriptions (id, email, topic, status, last_sent, created, expires) "
                     "VALUES (?,?,?,?,?,?,?)", (uuid.uuid4().hex, s["email"], topic, "active", 0, t, expires))
            expiries[topic] = expires
            if plan["price_cents"] > 0:      # every purchase / renewal is its own history line
                db.x("INSERT INTO sub_history (id, email, topic, price_cents, subscribed) VALUES (?,?,?,?,?)",
                     (uuid.uuid4().hex, s["email"], topic, plan["price_cents"], t))
            else:
                _open_history(s["email"], topic, 0)
    except Exception:
        if amount > 0:
            payments.refund(last4, amount)                          # never keep money without a subscription
            db.x("UPDATE payments SET status='refunded' WHERE id=?", (pid,))
        raise ApiError(500, "could not activate subscription, card was not charged")
    try:
        lines = "\n".join(f"- {p['title']}: ${p['price_cents'] / 100:.2f}" for p in plans)
        tail = f"Total ${amount / 100:.2f} (TEST payment) charged to card ending {last4}." if amount else "No charge."
        messaging.send_email(s["email"], "Receipt: " + ", ".join(p["title"] for p in plans),
                             f"Thanks! You are subscribed to:\n{lines}\n\n{tail}")
    except Exception as e:
        print("receipt email failed:", e, flush=True)
    bal = None
    if last4:
        bal = db.one("SELECT balance_cents FROM test_cards WHERE number LIKE ?", ("%" + last4,))["balance_cents"]
    first = topics[0]
    return {"ok": True, "topics": topics, "topic": first, "paid_cents": amount, "card_last4": last4,
            "expires": expiries[first], "expiries": expiries, "card_balance_cents": bal}


def payments_list(data, req):
    s = sec.get_session(req, "full")
    return {"payments": db.q("SELECT u.email, p.topic, p.card_last4, p.amount_cents, p.status, p.reason, p.created "
                             "FROM payments p JOIN users u ON u.id=p.user_id "
                             "WHERE p.user_id=? ORDER BY p.created DESC LIMIT 50", (s["user_id"],))}


def admin_topup(data, req):
    if not config.PUBLISH_KEY or not sec.eq(req.headers.get("x-publish-key", ""), config.PUBLISH_KEY):
        raise ApiError(403, "forbidden")
    n = payments.topup(data.get("card"), None if data.get("amount") is None else int(float(data["amount"]) * 100))
    return {"ok": True, "cards_updated": n}


# ---------------------------------------------------------------- misc
def dev_outbox(data, req):
    if config.MESSAGING != "console":
        raise ApiError(404, "not found")
    to = req.query_params.get("to")
    rows = db.q("SELECT channel, recipient, subject, body FROM outbox " +
                ("WHERE recipient=? " if to else "") + "ORDER BY created", (to,) if to else ())
    return {"messages": rows}


async def index(request):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


async def twofa_page(request):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "2fa.html"))


async def health(request):
    return JSONResponse({"ok": True})


@asynccontextmanager
async def lifespan(app):
    db.init()
    payments.seed()
    yield


app = Starlette(lifespan=lifespan, routes=[
    Route("/", index),
    Route("/healthz", health),
    Route("/api/register", api(register), methods=["POST"]),
    Route("/api/verify", api(verify), methods=["POST"]),
    Route("/api/login", api(login), methods=["POST"]),
    Route("/api/login/verify", api(login_verify), methods=["POST"]),
    Route("/api/2fa/verify", api(tfa_verify), methods=["POST"]),
    Route("/api/2fa/setup", api(tfa_setup), methods=["POST"]),
    Route("/api/2fa/enable", api(tfa_enable), methods=["POST"]),
    Route("/api/2fa/disable", api(tfa_disable), methods=["POST"]),
    Route("/api/2fa/recovery", api(tfa_regenerate), methods=["POST"]),
    Route("/2fa", twofa_page),
    Route("/api/me", api(me), methods=["GET"]),
    Route("/api/logout", api(logout), methods=["POST"]),
    Route("/api/subscribe", api(subscribe), methods=["POST"]),
    Route("/api/config", api(app_config), methods=["GET"]),
    Route("/api/plans", api(plans_list), methods=["GET"]),
    Route("/api/test-cards", api(test_cards), methods=["GET"]),
    Route("/api/checkout", api(checkout), methods=["POST"]),
    Route("/api/payments", api(payments_list), methods=["GET"]),
    Route("/api/history", api(history), methods=["GET"]),
    Route("/api/feed", api(feed), methods=["GET"]),
    Route("/api/admin/topup", api(admin_topup), methods=["POST"]),
    Route("/confirm", confirm, methods=["GET"]),
    Route("/unsubscribe", unsubscribe, methods=["GET"]),
    Route("/api/unsubscribe", unsubscribe_api, methods=["POST"]),
    Route("/api/publish", publish, methods=["POST"]),
    Route("/api/stream", stream, methods=["GET"]),
    Route("/dev/outbox", api(dev_outbox), methods=["GET"]),
])
