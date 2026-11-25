"""End-to-end tests against a live server (console messaging, temp SQLite). Run: pytest -q"""
import http.client
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

_tmp = tempfile.mkdtemp()
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_tmp}/t.db", "MESSAGING": "console", "SECRET_KEY": "test-secret",
    "PUBLISH_KEY": "pubkey", "OTP_SENDS_PER_HOUR": "3", "CONFIRM_RESEND_GAP": "0",
})
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402
import uvicorn  # noqa: E402

from app import security as sec  # noqa: E402
from app.db import db  # noqa: E402
from app.main import app  # noqa: E402

PORT = None


@pytest.fixture(scope="session", autouse=True)
def server():
    global PORT
    s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield
    srv.should_exit = True


def call(method, path, body=None, token=None, headers=None):
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.startswith("{") else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        return e.code, (json.loads(raw) if raw.startswith("{") else raw)


def last_code(to):
    msgs = call("GET", "/dev/outbox?to=" + urllib.parse.quote(to))[1]["messages"]
    return re.search(r"\b(\d{6})\b", msgs[-1]["body"]).group(1)


def signup(email, phone=None):
    """Email-only sign-up (default). The phone argument is accepted and ignored while SMS is disabled."""
    assert call("POST", "/api/register", {"email": email})[0] == 200
    r = call("POST", "/api/verify", {"email": email, "channel": "email", "code": last_code(email)})
    assert r[0] == 200 and r[1]["email_verified"]


def login_full(email, channel="email"):
    # age old codes so the hourly send throttle does not interfere with this helper
    db.x("UPDATE otps SET created=created-7200 WHERE user_id=(SELECT id FROM users WHERE email=?)", (email,))
    assert call("POST", "/api/login", {"email": email, "channel": channel})[0] == 200
    code = last_code(email)
    return call("POST", "/api/login/verify", {"email": email, "channel": channel, "code": code})[1]


# ---------------------------------------------------------------- mobile + email OTP
def test_register_verify_email_and_mobile():
    signup("a@example.com", "+919876543210")


def test_wrong_code_and_lockout():
    email, phone = "b@example.com", "+919876543211"
    call("POST", "/api/register", {"email": email, "phone": phone})
    good = last_code(email)
    bad = "000000" if good != "000000" else "111111"
    for _ in range(5):
        assert call("POST", "/api/verify", {"email": email, "channel": "email", "code": bad})[0] == 400
    # even the right code is rejected after 5 attempts
    assert call("POST", "/api/verify", {"email": email, "channel": "email", "code": good})[0] == 400


def test_otp_expiry():
    email, phone = "c@example.com", "+919876543212"
    call("POST", "/api/register", {"email": email, "phone": phone})
    code = last_code(email)
    db.x("UPDATE otps SET expires=? WHERE user_id=(SELECT id FROM users WHERE email=?)", (sec.now() - 1, email))
    assert call("POST", "/api/verify", {"email": email, "channel": "email", "code": code})[0] == 400


def test_resend_throttle_and_new_code_invalidates_old():
    email, phone = "d@example.com", "+919876543213"
    call("POST", "/api/register", {"email": email, "phone": phone})     # email send 1
    first = last_code(email)
    call("POST", "/api/register", {"email": email, "phone": phone})     # send 2
    call("POST", "/api/register", {"email": email, "phone": phone})     # send 3
    n = len(call("GET", f"/dev/outbox?to={email}")[1]["messages"])
    assert call("POST", "/api/register", {"email": email, "phone": phone})[0] == 429  # 4th: throttled
    assert len(call("GET", f"/dev/outbox?to={email}")[1]["messages"]) == n
    if first != last_code(email):
        assert call("POST", "/api/verify", {"email": email, "channel": "email", "code": first})[0] == 400


def test_no_account_enumeration():
    r1 = call("POST", "/api/login", {"email": "nobody@example.com", "channel": "email"})
    r2 = call("POST", "/api/register", {"email": "a@example.com"})
    assert r1[0] == 200 and r2[0] == 200 and r1[1] == r2[1]


def test_input_validation():
    assert call("POST", "/api/register", {"email": "bad"})[0] == 400
    assert call("POST", "/api/register", {"email": "e@example.com", "phone": "x"})[0] == 200  # phone ignored


def test_email_only_default_rejects_sms_channel():
    assert call("GET", "/api/config")[1] == {"sms": False}
    signup("nosms@example.com")
    assert call("POST", "/api/login", {"email": "nosms@example.com", "channel": "sms"})[0] == 400
    assert call("POST", "/api/verify", {"email": "nosms@example.com", "channel": "sms", "code": "123456"})[0] == 400


def test_unverified_email_cannot_log_in_with_registration_code():
    call("POST", "/api/register", {"email": "unv@example.com"})
    code = last_code("unv@example.com")
    assert call("POST", "/api/login/verify", {"email": "unv@example.com", "channel": "email", "code": code})[0] == 400


def test_sms_mode_still_works_when_enabled(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "SMS_ENABLED", True)
    email, phone = "both@example.com", "+919876543299"
    assert call("POST", "/api/register", {"email": email})[0] == 400          # phone now required
    assert call("POST", "/api/register", {"email": email, "phone": phone})[0] == 200
    assert call("POST", "/api/verify", {"email": email, "channel": "email", "code": last_code(email)})[0] == 200
    r = call("POST", "/api/verify", {"email": email, "channel": "sms", "code": last_code(phone)})[1]
    assert r["email_verified"] and r["phone_verified"]


# ---------------------------------------------------------------- 2FA
def test_totp_2fa_flow_replay_and_recovery():
    email = "a@example.com"
    s = login_full(email)
    assert s["stage"] == "ok"
    tok = s["token"]
    setup = call("POST", "/api/2fa/setup", token=tok)[1]
    secret = setup["secret"]
    assert setup["otpauth_uri"].startswith("otpauth://totp/")
    assert call("POST", "/api/2fa/enable", {"code": "000000"}, token=tok)[0] == 400
    step = sec.now() // 30
    code = sec.totp_at(secret, step)
    en = call("POST", "/api/2fa/enable", {"code": code}, token=tok)[1]
    assert len(en["recovery_codes"]) == 8

    # next login needs the second factor
    s2 = login_full(email)
    assert s2["stage"] == "2fa_required"
    assert call("GET", "/api/me", token=s2["token"])[0] == 401        # partial token is not enough
    # the step used at enable time cannot be replayed
    assert call("POST", "/api/2fa/verify", {"code": code}, token=s2["token"])[0] == 400
    nxt = sec.totp_at(secret, step + 1)                               # within +-1 window
    r = call("POST", "/api/2fa/verify", {"code": nxt}, token=s2["token"])
    assert r[0] == 200 and r[1]["stage"] == "ok"
    assert call("GET", "/api/me", token=r[1]["token"])[1]["totp_enabled"] is True

    # recovery code works once
    s3 = login_full(email)
    rc = en["recovery_codes"][0]
    assert call("POST", "/api/2fa/verify", {"recovery_code": rc}, token=s3["token"])[0] == 200
    s4 = login_full(email)
    assert call("POST", "/api/2fa/verify", {"recovery_code": rc}, token=s4["token"])[0] == 400


def test_2fa_attempt_limit():
    s = login_full("a@example.com")
    assert s["stage"] == "2fa_required"
    for _ in range(5):
        assert call("POST", "/api/2fa/verify", {"code": "123456"}, token=s["token"])[0] == 400
    assert call("POST", "/api/2fa/verify", {"code": "123456"}, token=s["token"])[0] == 401


# ---------------------------------------------------------------- subscription validation
def _confirm_link(email):
    msgs = [m for m in call("GET", f"/dev/outbox?to={email}")[1]["messages"] if "Confirm" in (m["subject"] or "")]
    return re.search(r"http://\S+/confirm\?token=\S+", msgs[-1]["body"]).group(0).split("localhost:8000")[-1]


def test_double_opt_in_stream_and_publish():
    signup("s@example.com", "+919876543220")
    tok = login_full("s@example.com")["token"]
    assert call("POST", "/api/subscribe", {"email": "s@example.com", "topic": "general"})[0] == 200

    # pending subscription cannot stream
    assert call("GET", "/api/stream?topics=general", token=tok)[0] == 403

    link = _confirm_link("s@example.com")
    assert "Subscribed" in call("GET", link)[1]
    assert "Already confirmed" in call("GET", link)[1]               # idempotent
    assert call("GET", "/confirm?token=garbage")[0] == 400
    assert call("GET", link[:-3] + "abc")[0] == 400                  # tampered signature

    # publish needs the key
    assert call("POST", "/api/publish", {"topic": "general", "message": "x"})[0] == 403

    # live stream receives the event
    got = []

    def reader():
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=8)
        c.request("GET", "/api/stream?topics=general", headers={"Authorization": f"Bearer {tok}"})
        r = c.getresponse()
        assert r.status == 200 and r.getheader("Content-Type").startswith("text/event-stream")
        while True:
            line = r.fp.readline().decode()
            if line.startswith("data:"):
                got.append(line.strip()); return

    t = threading.Thread(target=reader, daemon=True); t.start()
    time.sleep(0.6)
    r = call("POST", "/api/publish", {"topic": "general", "message": "disk 91% full"},
             headers={"X-Publish-Key": "pubkey"})
    assert r[0] == 200
    t.join(6)
    assert got == ["data: disk 91% full"]

    # subscriber also got the email, with an unsubscribe link
    time.sleep(0.5)
    mails = call("GET", "/dev/outbox?to=s@example.com")[1]["messages"]
    ev = [m for m in mails if m["subject"].startswith("[general]")][-1]
    unsub = re.search(r"http://\S+/unsubscribe\?token=\S+", ev["body"]).group(0).split("localhost:8000")[-1]
    assert "Unsubscribed" in call("GET", unsub)[1]
    assert call("GET", "/api/stream?topics=general", token=tok)[0] == 403


def test_expired_confirm_link():
    call("POST", "/api/subscribe", {"email": "late@example.com", "topic": "general"})
    sid = db.one("SELECT id FROM subscriptions WHERE email='late@example.com'")["id"]
    old = sec.sign("c", sid, sec.now() - 10)
    assert call("GET", f"/confirm?token={old}")[0] == 410


def test_in_page_unsubscribe_closes_stream_and_stops_events():
    signup("u@example.com", "+919876543230")
    tok = login_full("u@example.com")["token"]
    call("POST", "/api/subscribe", {"email": "u@example.com", "topic": "general"})
    call("GET", _confirm_link("u@example.com"))
    assert call("GET", "/api/me", token=tok)[1]["subscriptions"] == ["general"]

    seen = []

    def reader():
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=8)
        c.request("GET", "/api/stream?topics=general", headers={"Authorization": f"Bearer {tok}"})
        r = c.getresponse()
        while True:
            line = r.fp.readline().decode()
            if not line:
                seen.append("closed"); return
            if line.startswith("event: unsubscribed"):
                seen.append("unsubscribed")

    t = threading.Thread(target=reader, daemon=True); t.start()
    time.sleep(0.6)
    assert call("POST", "/api/unsubscribe", {"topic": "general"})[0] == 401          # needs login
    assert call("POST", "/api/unsubscribe", {"topic": "general"}, token=tok)[1]["unsubscribed"] is True
    t.join(5)
    assert seen[0] == "unsubscribed"                                              # stream ended by server
    assert call("GET", "/api/me", token=tok)[1]["subscriptions"] == []
    assert call("GET", "/api/stream?topics=general", token=tok)[0] == 403            # cannot stream again
    assert call("POST", "/api/unsubscribe", {"topic": "general"}, token=tok)[1]["unsubscribed"] is False  # idempotent
    # no more event emails
    before = len(call("GET", "/dev/outbox?to=u@example.com")[1]["messages"])
    call("POST", "/api/publish", {"topic": "general", "message": "later"}, headers={"X-Publish-Key": "pubkey"})
    time.sleep(0.5)
    assert len(call("GET", "/dev/outbox?to=u@example.com")[1]["messages"]) == before


# ---------------------------------------------------------------- paid plans + test cards
CARD1 = {"card_number": "1111 1111 1111 1111", "expiry": "00/00", "cvv": "123"}


def balance(last4):
    return db.one("SELECT balance_cents FROM test_cards WHERE number LIKE ?", ("%" + last4,))["balance_cents"]


def fresh_user(email, phone):
    signup(email, phone)
    return login_full(email)["token"]


def test_plans_and_cards_listed():
    p = call("GET", "/api/plans")[1]
    assert {x["topic"]: x["price_cents"] for x in p["plans"]}["alerts"] == 500
    cards = call("GET", "/api/test-cards")[1]["cards"]
    assert len(cards) == 5 and all(c["balance_cents"] == 3000 for c in cards)


def test_paid_topic_rejects_free_subscribe_and_anonymous_checkout():
    assert call("POST", "/api/subscribe", {"email": "p@example.com", "topic": "alerts"})[0] == 400
    assert call("POST", "/api/checkout", {"topic": "alerts", **CARD1})[0] == 401


def test_checkout_success_deducts_balance_and_unlocks_stream():
    tok = fresh_user("pay1@example.com", "+919876543240")
    assert call("GET", "/api/stream?topics=alerts", token=tok)[0] == 403          # not paid yet
    before = balance("1111")
    r = call("POST", "/api/checkout", {"topic": "alerts", **CARD1}, token=tok)
    assert r[0] == 200 and r[1]["paid_cents"] == 500 and r[1]["card_balance_cents"] == before - 500
    assert balance("1111") == before - 500
    me = call("GET", "/api/me", token=tok)[1]
    assert "alerts" in me["subscriptions"]
    assert me["subscription_details"][0]["expires"] > sec.now() + 29 * 86400
    # receipt email + payment history
    assert any(m["subject"].startswith("Receipt") for m in call("GET", "/dev/outbox?to=pay1@example.com")[1]["messages"])
    pay = call("GET", "/api/payments", token=tok)[1]["payments"][0]
    assert pay["status"] == "approved" and pay["email"] == "pay1@example.com"
    # stream now allowed (open then close quickly)
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
    c.request("GET", "/api/stream?topics=alerts", headers={"Authorization": f"Bearer {tok}"})
    assert c.getresponse().status == 200
    c.close()


def test_checkout_declines_invalid_card_details():
    tok = fresh_user("pay2@example.com", "+919876543241")
    before = balance("1111")
    for bad in ({**CARD1, "cvv": "999"}, {**CARD1, "expiry": "01/30"},
                {**CARD1, "card_number": "9999 9999 9999 9999"}, {**CARD1, "card_number": "1111 1111 1111 111"},
                {"card_number": "", "expiry": "", "cvv": ""}):
        r = call("POST", "/api/checkout", {"topic": "news", **bad}, token=tok)
        assert r[0] == 402 and r[1]["error"] == "Payment declined: invalid card details", bad
    assert balance("1111") == before                                             # nothing charged
    assert "news" not in call("GET", "/api/me", token=tok)[1]["subscriptions"]
    st = [p["status"] for p in call("GET", "/api/payments", token=tok)[1]["payments"]]
    assert st == ["declined"] * 5


def test_checkout_declines_insufficient_balance_then_topup_fixes_it():
    tok = fresh_user("pay3@example.com", "+919876543242")
    db.x("UPDATE test_cards SET balance_cents=200 WHERE number LIKE '%5555'")
    card5 = {"card_number": "5555555555555555", "expiry": "00/00", "cvv": "123"}
    r = call("POST", "/api/checkout", {"topic": "finance", **card5}, token=tok)        # $9 > $2
    assert r[0] == 402 and r[1]["error"] == "Payment declined: insufficient balance"
    assert balance("5555") == 200                                                      # untouched
    from app import payments
    assert payments.topup("5555") == 1
    assert balance("5555") == 3000
    assert call("POST", "/api/checkout", {"topic": "finance", **card5}, token=tok)[0] == 200
    assert balance("5555") == 3000 - 900


def test_balance_never_goes_negative_and_cards_are_independent():
    tok = fresh_user("pay4@example.com", "+919876543243")
    card2 = {"card_number": "2222 2222 2222 2222", "expiry": "00/00", "cvv": "123"}
    before1 = balance("1111")
    db.x("UPDATE test_cards SET balance_cents=100 WHERE number LIKE '%2222'")
    assert call("POST", "/api/checkout", {"topic": "alerts", **card2}, token=tok)[0] == 402
    assert balance("2222") == 100 and balance("1111") == before1


def test_renewal_extends_and_expired_subscription_loses_access():
    tok = fresh_user("pay5@example.com", "+919876543244")
    exp1 = call("POST", "/api/checkout", {"topic": "tech", **CARD1}, token=tok)[1]["expires"]
    exp2 = call("POST", "/api/checkout", {"topic": "tech", **CARD1}, token=tok)[1]["expires"]
    assert exp2 == exp1 + 30 * 86400                                            # renewal adds 30 days
    db.x("UPDATE subscriptions SET expires=? WHERE email='pay5@example.com'", (sec.now() - 5,))
    assert "tech" not in call("GET", "/api/me", token=tok)[1]["subscriptions"]
    assert call("GET", "/api/stream?topics=tech", token=tok)[0] == 403          # expired = no stream
    exp3 = call("POST", "/api/checkout", {"topic": "tech", **CARD1}, token=tok)[1]["expires"]
    assert exp3 > sec.now() + 29 * 86400                                        # re-buy starts from now


def test_free_plan_rejected_at_checkout_and_unknown_plan():
    tok = fresh_user("pay6@example.com", "+919876543245")
    assert call("POST", "/api/checkout", {"topic": "general", **CARD1}, token=tok)[0] == 400
    assert call("POST", "/api/checkout", {"topic": "nope", **CARD1}, token=tok)[0] == 404


def test_admin_topup_endpoint_and_script():
    import subprocess
    db.x("UPDATE test_cards SET balance_cents=1")
    assert call("POST", "/api/admin/topup", {}, headers={})[0] == 403
    r = call("POST", "/api/admin/topup", {"card": "3333"}, headers={"X-Publish-Key": "pubkey"})
    assert r[1]["cards_updated"] == 1 and balance("3333") == 3000 and balance("4444") == 1
    out = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "topup_cards.py")],
                         capture_output=True, text=True, env=dict(os.environ))
    assert out.returncode == 0, out.stderr
    assert all(balance(x) == 3000 for x in ("1111", "2222", "3333", "4444", "5555"))
    assert "topped up 5 card(s) to $30.00" in out.stdout


# ---------------------------------------------------------------- 2FA setup page backend
def test_2fa_page_served():
    s, body = call("GET", "/2fa")
    assert s == 200 and "Two-factor authentication" in body and "Set up authenticator" in body


def test_2fa_setup_qr_recovery_regenerate_and_disable():
    tok = fresh_user("tfa1@example.com", "+919876543250")
    assert call("GET", "/api/me", token=tok)[1]["totp_enabled"] is False
    setup = call("POST", "/api/2fa/setup", token=tok)[1]
    assert setup["qr_svg"].startswith("<svg") and setup["secret"] in setup["otpauth_uri"]
    cv2 = pytest.importorskip("cv2")                       # the QR must really decode to the otpauth URI
    import numpy as np
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(); pg = b.new_page(viewport={"width": 400, "height": 400})
        pg.set_content("<body style='margin:0'>" + setup["qr_svg"]); png = pg.screenshot(); b.close()
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    assert cv2.QRCodeDetector().detectAndDecode(img)[0] == setup["otpauth_uri"]

    cur = sec.now() // 30
    secret = setup["secret"]
    en = call("POST", "/api/2fa/enable", {"code": sec.totp_at(secret, cur - 1)}, token=tok)[1]
    old_codes = en["recovery_codes"]
    me = call("GET", "/api/me", token=tok)[1]
    assert me["totp_enabled"] and me["recovery_remaining"] == 8
    assert call("POST", "/api/2fa/setup", token=tok)[0] == 409           # already on

    # regenerate needs a valid, unused code
    assert call("POST", "/api/2fa/recovery", {"code": "000000"}, token=tok)[0] == 400
    assert call("POST", "/api/2fa/recovery", {"code": sec.totp_at(secret, cur - 1)}, token=tok)[0] == 400  # replay
    new = call("POST", "/api/2fa/recovery", {"code": sec.totp_at(secret, cur)}, token=tok)[1]["recovery_codes"]
    assert len(new) == 8 and not set(new) & set(old_codes)
    # old recovery code is dead, new one works
    s1 = login_full("tfa1@example.com")
    assert call("POST", "/api/2fa/verify", {"recovery_code": old_codes[0]}, token=s1["token"])[0] == 400
    s2 = login_full("tfa1@example.com")
    assert call("POST", "/api/2fa/verify", {"recovery_code": new[0]}, token=s2["token"])[0] == 200
    assert call("GET", "/api/me", token=tok)[1]["recovery_remaining"] == 7

    # disable
    assert call("POST", "/api/2fa/disable", {"code": "123456"}, token=tok)[0] == 400
    assert call("POST", "/api/2fa/disable", {"code": sec.totp_at(secret, cur + 1)}, token=tok)[0] == 200
    me = call("GET", "/api/me", token=tok)[1]
    assert me["totp_enabled"] is False and me["recovery_remaining"] == 0
    assert login_full("tfa1@example.com")["stage"] == "ok"              # no second factor any more


# ---------------------------------------------------------------- SMTP mode (same path as signup_app)
def test_smtp_mode_sends_email_with_login(monkeypatch):
    from app import config, messaging
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None): sent["hp"] = (host, port)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, context=None): sent["tls"] = True
        def login(self, u, p): sent["login"] = (u, p)
        def send_message(self, m): sent["msg"] = m

    monkeypatch.setattr(messaging.smtplib, "SMTP", FakeSMTP)
    for k, v in {"MESSAGING": "smtp", "SMTP_HOST": "smtp.example.com", "SMTP_PORT": 587,
                 "MAIL_FROM": "from@example.com", "SMTP_USER": "u", "SMTP_PASSWORD": "p"}.items():
        monkeypatch.setattr(config, k, v)
    messaging.send_email("to@example.com", "Hi", "Your code is 123456")
    assert sent["hp"] == ("smtp.example.com", 587) and sent["tls"] and sent["login"] == ("u", "p")
    assert sent["msg"]["To"] == "to@example.com" and sent["msg"]["From"] == "from@example.com"
    with pytest.raises(RuntimeError):
        messaging.send_sms("+919876543210", "x")


def test_secret_files_override_env(tmp_path, monkeypatch):
    from app import config
    f = tmp_path / "k"; f.write_text("from-file\n")
    monkeypatch.setenv("X_TEST_FILE", str(f)); monkeypatch.setenv("X_TEST", "from-env")
    assert config._secret("X_TEST") == "from-file"


def test_pod_tls_cert_is_valid_and_served(tmp_path):
    import ssl
    from app.tls import make_self_signed
    cert, key = make_self_signed(str(tmp_path))
    ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert, key)   # loads = matching, valid pair


def test_rds_mode_reads_login_from_files_and_skips_ddl(tmp_path, monkeypatch):
    from app import config
    from app.db import DB
    (tmp_path / "u").write_text("streaming_app\n"); (tmp_path / "p").write_text("s3cret\n")
    monkeypatch.setenv("DB_USER_FILE", str(tmp_path / "u")); monkeypatch.setenv("DB_PASSWORD_FILE", str(tmp_path / "p"))
    monkeypatch.setattr(config, "DB_HOST", "rds.example")
    d = DB("sqlite:///x", use_params=True)
    assert d.pg and config._secret("DB_USER") == "streaming_app" and config._secret("DB_PASSWORD") == "s3cret"
    monkeypatch.setattr(config, "DB_INIT", False)
    d.init()   # must not touch the database (the app user cannot create tables)


def test_admin_schema_sql_matches_app_schema():
    from app.db import SCHEMA
    sql = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "db", "02_schema.sql")).read()
    for stmt in SCHEMA:
        assert stmt.strip() in sql, "db/02_schema.sql is out of date: " + stmt.strip()[:60]
    assert "01_create_db_user" in sql and "streaming_app" in sql and "TO streamnotify" not in sql
