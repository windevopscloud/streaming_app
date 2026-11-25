#!/usr/bin/env python3
"""Reset test-card balances to the default $30 (or --amount). Uses the app's own DB settings.

  python scripts/topup_cards.py                 # all 5 cards -> $30
  python scripts/topup_cards.py --card 3333     # one card
  python scripts/topup_cards.py --amount 250    # all cards -> $250
In the cluster:  kubectl exec deploy/streamnotify -- python scripts/topup_cards.py
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, payments  # noqa: E402
from app.db import db  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--card", help="last 4 digits of one card (default: all)")
ap.add_argument("--amount", type=float, default=config.CARD_START_BALANCE_CENTS / 100, help="dollars (default $30)")
a = ap.parse_args()

db.init()
payments.seed()
n = payments.topup(a.card, int(round(a.amount * 100)))
print(f"topped up {n} card(s) to ${a.amount:.2f}")
for c in payments.list_cards():
    print(f"  {c['number'][:4]} ... {c['number'][-4:]}   ${c['balance_cents'] / 100:.2f}")
