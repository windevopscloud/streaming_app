import os


def _int(name, default):
    return int(os.environ.get(name, default))


DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./streamnotify.db")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
MESSAGING = os.environ.get("MESSAGING", "console")  # console | aws
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
SES_FROM = os.environ.get("SES_FROM", "")
SNS_SENDER_ID = os.environ.get("SNS_SENDER_ID", "")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
PUBLISH_KEY = os.environ.get("PUBLISH_KEY", "")

OTP_TTL = _int("OTP_TTL", 300)             # seconds
OTP_MAX_ATTEMPTS = _int("OTP_MAX_ATTEMPTS", 5)
OTP_SENDS_PER_HOUR = _int("OTP_SENDS_PER_HOUR", 3)
PARTIAL_SESSION_TTL = _int("PARTIAL_SESSION_TTL", 300)
FULL_SESSION_TTL = _int("FULL_SESSION_TTL", 8 * 3600)
TFA_MAX_ATTEMPTS = _int("TFA_MAX_ATTEMPTS", 5)
CONFIRM_TTL = _int("CONFIRM_TTL", 24 * 3600)
CONFIRM_RESEND_GAP = _int("CONFIRM_RESEND_GAP", 60)

if MESSAGING == "aws" and SECRET_KEY == "dev-only-change-me":
    raise RuntimeError("SECRET_KEY must be set when MESSAGING=aws")

PAYMENT_MODE = os.environ.get("PAYMENT_MODE", "test")   # test = dummy cards only
SUB_DAYS = _int("SUB_DAYS", 30)
CARD_START_BALANCE_CENTS = _int("CARD_START_BALANCE_CENTS", 3000)   # $30
