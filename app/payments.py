"""TEST-MODE payments only. Five dummy cards live in the DB; nothing real is ever charged or collected."""
import re

from . import config
from .db import db
from .security import eq

# (number, expiry, cvv) - public dummy values, shown on the checkout page
TEST_CARDS = [(str(d) * 16, "00/00", "123") for d in (1, 2, 3, 4, 5)]

PLANS = [
    ("general", "General updates", "Free community updates (email confirmation required)", 0),
    ("alerts", "System alerts", "Incident and capacity alerts", 500),
    ("news", "Daily news", "Headlines digest", 300),
    ("sports", "Sports scores", "Live scores and results", 400),
    ("tech", "Tech watch", "Releases and outages", 600),
    ("finance", "Market ticker", "Index and price moves", 900),
]


def seed():
    for n, e, c in TEST_CARDS:
        if not db.one("SELECT number FROM test_cards WHERE number=?", (n,)):
            db.x("INSERT INTO test_cards (number, expiry, cvv, balance_cents) VALUES (?,?,?,?)",
                 (n, e, c, config.CARD_START_BALANCE_CENTS))
    for topic, title, desc, price in PLANS:
        if not db.one("SELECT topic FROM plans WHERE topic=?", (topic,)):
            db.x("INSERT INTO plans (topic, title, description, price_cents) VALUES (?,?,?,?)",
                 (topic, title, desc, price))


def list_cards():
    return db.q("SELECT number, expiry, cvv, balance_cents FROM test_cards ORDER BY number")


def topup(last4=None, cents=None):
    """Reset card balance(s) to the default (or `cents`). Returns number of cards updated."""
    cents = config.CARD_START_BALANCE_CENTS if cents is None else int(cents)
    if last4:
        return db.x("UPDATE test_cards SET balance_cents=? WHERE number LIKE ?", (cents, "%" + str(last4)[-4:]))
    return db.x("UPDATE test_cards SET balance_cents=?", (cents,))


def charge(number, expiry, cvv, cents):
    """-> (ok, reason, last4). reason: approved | invalid_card | insufficient_balance."""
    num = re.sub(r"[\s-]", "", str(number or ""))
    card = db.one("SELECT * FROM test_cards WHERE number=?", (num,)) if num.isdigit() else None
    if (not card or card["expiry"] != str(expiry or "").strip()
            or not eq(card["cvv"], str(cvv or "").strip())):
        return False, "invalid_card", None
    last4 = num[-4:]
    # atomic: the balance can never go below zero, even with concurrent checkouts
    if db.x("UPDATE test_cards SET balance_cents=balance_cents-? WHERE number=? AND balance_cents>=?",
            (cents, num, cents)) != 1:
        return False, "insufficient_balance", last4
    return True, "approved", last4


def refund(last4, cents):
    db.x("UPDATE test_cards SET balance_cents=balance_cents+? WHERE number LIKE ?", (cents, "%" + last4))
