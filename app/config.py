import os


def _int(name, default):
    return int(os.environ.get(name, default))


def _secret(name, default=""):
    """NAME_FILE (a mounted Secrets Manager file) wins over NAME."""
    path = os.environ.get(f"{name}_FILE")
    if path:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    return os.environ.get(name, default)


DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./streamnotify.db")
SECRET_KEY = _secret("SECRET_KEY", "dev-only-change-me")
MESSAGING = os.environ.get("MESSAGING", "console")  # console | smtp | aws
SMS_ENABLED = os.environ.get("SMS_ENABLED", "0") == "1"  # 0: email OTP only (default); 1: also require/verify a mobile number
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
SES_FROM = os.environ.get("SES_FROM", "")
SNS_SENDER_ID = os.environ.get("SNS_SENDER_ID", "")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
PUBLISH_KEY = _secret("PUBLISH_KEY", "")

# MESSAGING=smtp (same as signup_app): Gmail/SES SMTP over 587, credentials from SMTP_USER_FILE / SMTP_PASSWORD_FILE
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = _int("SMTP_PORT", 587)
SMTP_STARTTLS = os.environ.get("SMTP_STARTTLS", "true").lower() == "true"
MAIL_FROM = os.environ.get("MAIL_FROM", "")
SMTP_USER = _secret("SMTP_USER", "")
SMTP_PASSWORD = _secret("SMTP_PASSWORD", "")

OTP_TTL = _int("OTP_TTL", 300)             # seconds
OTP_MAX_ATTEMPTS = _int("OTP_MAX_ATTEMPTS", 5)
OTP_SENDS_PER_HOUR = _int("OTP_SENDS_PER_HOUR", 3)
PARTIAL_SESSION_TTL = _int("PARTIAL_SESSION_TTL", 300)
FULL_SESSION_TTL = _int("FULL_SESSION_TTL", 8 * 3600)
TFA_MAX_ATTEMPTS = _int("TFA_MAX_ATTEMPTS", 5)
CONFIRM_TTL = _int("CONFIRM_TTL", 24 * 3600)
CONFIRM_RESEND_GAP = _int("CONFIRM_RESEND_GAP", 60)

if MESSAGING not in ("console", "smtp", "aws"):
    raise RuntimeError("MESSAGING must be console, smtp or aws")
if MESSAGING != "console" and SECRET_KEY == "dev-only-change-me":
    raise RuntimeError("SECRET_KEY must be set when MESSAGING is not console")
if MESSAGING == "smtp" and (not SMTP_HOST or not MAIL_FROM):
    raise RuntimeError("SMTP_HOST and MAIL_FROM are required when MESSAGING=smtp")
if MESSAGING == "smtp" and SMS_ENABLED:
    raise RuntimeError("SMS_ENABLED=1 needs MESSAGING=aws (SMTP cannot send SMS)")

PAYMENT_MODE = os.environ.get("PAYMENT_MODE", "test")   # test = dummy cards only
SUB_DAYS = _int("SUB_DAYS", 30)
CARD_START_BALANCE_CENTS = _int("CARD_START_BALANCE_CENTS", 3000)   # $30
