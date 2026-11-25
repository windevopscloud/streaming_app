# StreamNotify

Email OTP + authenticator-app 2FA (mobile SMS OTP is optional, off by default: `SMS_ENABLED=1`) + double opt-in email subscriptions + live feed (SSE).
Starlette service, SQLite locally / PostgreSQL (RDS) in the cluster, SES for email, SNS for SMS (only when `SMS_ENABLED=1`).

## Run locally (no AWS needed)
    pip install -r requirements.txt
    PUBLISH_KEY=pub123 uvicorn app.main:app --port 8000
    # open http://localhost:8000 ; codes and links print in the console and at GET /dev/outbox
    pytest -q                      # 22 end-to-end tests

Publish: `curl -X POST localhost:8000/api/publish -H "X-Publish-Key: pub123" -H "Content-Type: application/json" -d '{"topic":"alerts","message":"hello"}'`

## 2FA setup page (/2fa)
Log in on the main page, then open **/2fa**: install an authenticator app, scan the QR code (or type the setup key), enter the 6-digit code to turn it on,
then save the 8 one-time recovery codes (shown once; copy or download). The same page shows ON/OFF status, codes left, **new recovery codes**
and **turn off 2FA** (both need a current authenticator code). The QR is generated on the server (reportlab, no external service).

## Paid plans + TEST cards (no real payments)
Plans (monthly, 30-day access): general FREE (email double opt-in) - alerts $5 - news $3 - sports $4 - tech $6 - finance $9.
Log in, pick a plan, pay with one of 5 dummy cards (shown on the page; all expiry `00/00`, CVV `123`, $30 each):
`1111 1111 1111 1111` ... `5555 5555 5555 5555`.
- Wrong number / expiry / CVV -> **Payment declined: invalid card details**
- Not enough balance -> **Payment declined: insufficient balance** (balance is checked + deducted atomically, never negative)
- Success -> balance reduced, subscription active for 30 days, receipt email; buying again renews (+30 days); expired = no streaming.
- Unsubscribing keeps the user logged in; there is no refund in test mode.
Only these 5 cards exist in the DB; anything else is declined and nothing is stored. `PAYMENT_MODE=test` (default); any other value disables checkout.

Top up all cards back to $30:

    python scripts/topup_cards.py                 # all cards -> $30
    python scripts/topup_cards.py --card 3333     # one card
    python scripts/topup_cards.py --amount 250
    kubectl exec deploy/streamnotify -- python scripts/topup_cards.py        # in the cluster
    curl -X POST https://<host>/api/admin/topup -H "X-Publish-Key: <PUBLISH_KEY>" -H "Content-Type: application/json" -d '{}'

## API
POST /api/register, /api/verify, /api/login, /api/login/verify, /api/2fa/{setup,enable,verify,disable,recovery}, page /2fa,
/api/plans, /api/checkout, /api/payments, /api/test-cards, /api/admin/topup, /api/subscribe (free plan), /api/unsubscribe, /api/publish, /api/logout - GET /api/me, /api/stream?topics=, /confirm, /unsubscribe

## Deploy (EKS)
1. Add `terraform/streamnotify_iam.tf` to infra_aws/dev (IRSA role + SES identity); verify the SES sender via the email SES sends.
2. Create the DB: `CREATE DATABASE streamnotify;` on the RDS instance (tables are created at startup).
3. Build/push image `streamnotify` to ECR (add `streamnotify` to `ecr_repos`).
4. Create the `streamnotify-secret` (command in k8s/streamnotify.yaml), fill the `__PLACEHOLDERS__`, apply the yaml.
5. DNS: CNAME `__HOST__` to the public ALB, same as your other hostnames. Keep replicas at 1.
6. Optional: `kubectl apply -f k8s/publisher-cronjob.yaml` posts a test event every minute.

## AWS gotchas
- **SES sandbox**: new accounts can email only verified addresses (verify your test recipients, or request production access).
- **SNS SMS sandbox**: new accounts can text only verified destination numbers (SNS > Text messaging > Sandbox). Default spend limit is low.
- **India (+91)**: SMS to India needs DLT registration (entity, sender ID, template) before delivery works outside the sandbox.
- Streaming must not go through API Gateway (it buffers); this service sits behind the ALB + Istio directly.
- SSE page uses `?access_token=` because EventSource cannot set headers; for production move to a cookie.
