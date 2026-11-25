"""OTP, TOTP (RFC 6238), recovery codes, signed links and sessions. Stdlib only."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
import uuid

from . import config
from .db import db


class ApiError(Exception):
    def __init__(self, status, msg):
        self.status, self.msg = status, msg


def now():
    return int(time.time())


def _h(ctx, val):
    return hmac.new(config.SECRET_KEY.encode(), f"{ctx}:{val}".encode(), hashlib.sha256).hexdigest()


def eq(a, b):
    return hmac.compare_digest(str(a), str(b))


# ---------- signed, stateless links (confirm / unsubscribe) ----------
def sign(kind, *parts):
    body = ".".join([kind, *map(str, parts)])
    return body + "." + _h("sig", body)[:32]


def unsign(token):
    body, _, sig = (token or "").rpartition(".")
    if not body or not eq(sig, _h("sig", body)[:32]):
        return None
    return body.split(".")


# ---------- one-time codes (email / sms) ----------
def send_code(user, channel, send):
    """Create + send a 6-digit code. `send(code)` delivers it. Throttled per user+channel."""
    t = now()
    recent = db.one(
        "SELECT COUNT(*) AS n FROM otps WHERE user_id=? AND channel=? AND created>?",
        (user["id"], channel, t - 3600),
    )["n"]
    if recent >= config.OTP_SENDS_PER_HOUR:
        raise ApiError(429, "too many codes requested, try again later")
    db.x("UPDATE otps SET used=1 WHERE user_id=? AND channel=? AND used=0", (user["id"], channel))
    code = f"{secrets.randbelow(10**6):06d}"
    db.x(
        "INSERT INTO otps (id, user_id, channel, code_hash, expires, created) VALUES (?,?,?,?,?,?)",
        (uuid.uuid4().hex, user["id"], channel, _h(f"otp:{user['id']}:{channel}", code),
         t + config.OTP_TTL, t),
    )
    send(code)


def check_code(user, channel, code):
    """True if `code` matches the latest live code. Counts attempts; single use."""
    code = str(code or "").strip()
    row = db.one(
        "SELECT * FROM otps WHERE user_id=? AND channel=? AND used=0 AND expires>? "
        "ORDER BY created DESC LIMIT 1",
        (user["id"], channel, now()),
    )
    if not row or row["attempts"] >= config.OTP_MAX_ATTEMPTS:
        return False
    db.x("UPDATE otps SET attempts=attempts+1 WHERE id=?", (row["id"],))
    if not eq(row["code_hash"], _h(f"otp:{user['id']}:{channel}", code)):
        return False
    return db.x("UPDATE otps SET used=1 WHERE id=? AND used=0", (row["id"],)) == 1


# ---------- TOTP (authenticator app) ----------
def new_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode()  # 32 chars, no padding


def totp_at(secret, step):
    key = base64.b32decode(secret)
    h = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = h[-1] & 15
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 10**6:06d}"


def totp_match(secret, code, last_step=0):
    """Return matched step (window +-1, never reusing a step) or None."""
    code = str(code or "").strip()
    cur = now() // 30
    for step in (cur - 1, cur, cur + 1):
        if step > last_step and eq(totp_at(secret, step), code):
            return step
    return None


def otpauth_uri(email, secret):
    return f"otpauth://totp/StreamNotify:{email}?secret={secret}&issuer=StreamNotify&digits=6&period=30"


def make_recovery_codes(user_id, n=8):
    db.x("DELETE FROM recovery_codes WHERE user_id=?", (user_id,))
    codes = []
    for _ in range(n):
        c = secrets.token_hex(2) + "-" + secrets.token_hex(2)
        codes.append(c)
        db.x("INSERT INTO recovery_codes (id, user_id, code_hash) VALUES (?,?,?)",
             (uuid.uuid4().hex, user_id, _h(f"rec:{user_id}", c)))
    return codes


def use_recovery_code(user_id, code):
    code = str(code or "").strip().lower()
    return db.x("UPDATE recovery_codes SET used=1 WHERE user_id=? AND code_hash=? AND used=0",
                (user_id, _h(f"rec:{user_id}", code))) == 1


# ---------- sessions ----------
def create_session(user_id, stage):
    token = secrets.token_urlsafe(32)
    ttl = {"full": config.FULL_SESSION_TTL, "setup": config.SETUP_SESSION_TTL}.get(stage, config.PARTIAL_SESSION_TTL)
    db.x("INSERT INTO sessions (token_hash, user_id, stage, expires) VALUES (?,?,?,?)",
         (_h("sess", token), user_id, stage, now() + ttl))
    return token


def token_from(request):
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.query_params.get("access_token", "")


def get_session(request, stage):
    """`stage` is one stage name or a tuple of allowed stages."""
    allowed = (stage,) if isinstance(stage, str) else tuple(stage)
    token = token_from(request)
    row = db.one(
        "SELECT s.token_hash, s.user_id, s.stage, u.email FROM sessions s "
        "JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>?",
        (_h("sess", token), now()),
    ) if token else None
    if not row or row["stage"] not in allowed:
        raise ApiError(401, "not authenticated")
    return row
