# StreamNotify

Email OTP + authenticator-app 2FA (mobile SMS OTP is optional, off by default: `SMS_ENABLED=1`) + double opt-in email subscriptions + live feed (SSE).
Starlette service, SQLite locally / PostgreSQL (RDS) in the cluster, email over SMTP (same Gmail secret as signup_app) or SES; SNS for SMS only when `SMS_ENABLED=1`.

## Run locally (no AWS needed)
    pip install -r requirements.txt
    PUBLISH_KEY=pub123 uvicorn app.main:app --port 8000
    # open http://localhost:8000 ; codes and links print in the console and at GET /dev/outbox
    pytest -q                      # 27 end-to-end tests

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

## Deploy (EKS, same pattern as signup_app)
Layout: `k8s/base` + `k8s/overlays/poc` (kustomize, no placeholders), `deploy/apply.sh`, `.github/workflows/build-push.yml`.
1. **Runner**: this repo has its own runner `streaming_app-runner-poc` (init_aws workflow: `create_app_runner=yes`, `APP_GITHUB_REPO=streaming_app`).
2. **Secrets Manager** (infra_aws): the pod reads `ses/poc/smtp` (Gmail user + app password, already used by signup_app) and `streamnotify/poc/app` (`SECRET_KEY`, `PUBLISH_KEY`). The IAM role `streamnotify-role` must be allowed to read both, or the pod stays in ContainerCreating.
3. **Host + TLS**: ACM certificate for `stream.windevopscloud.com` in us-west-2 plus a CNAME to the ALB. The Ingress has no certificate ARN: the AWS Load Balancer Controller finds the certificate that matches the host.
4. **Build + deploy**: run the `build-push` workflow (`deploy=yes`). `deploy/apply.sh` writes a small kustomize overlay for that run (image = your account's ECR + the commit tag, IRSA role ARN) and applies it. Replicas stay at 1 (in-memory SSE broker).
5. Data is SQLite on an emptyDir (lost when the pod is replaced); set `DATABASE_URL` to an RDS PostgreSQL URL for anything lasting.
6. Test publisher: suspended CronJob. Run once: `kubectl create job --from=cronjob/streamnotify-publisher test-1`.

## AWS gotchas
- **Email**: `MESSAGING=smtp` is the same logic as signup_app (`SMTP_HOST`, `SMTP_PORT`, `MAIL_FROM`, `SMTP_USER_FILE`, `SMTP_PASSWORD_FILE`, STARTTLS on 587, which the node security group allows). `MESSAGING=aws` (SES API, port 443) cannot be reached from nodes without internet.
- **SNS SMS** (only with `SMS_ENABLED=1`, `MESSAGING=aws`): new accounts can text only verified destination numbers. India (+91) needs DLT registration.
- The live feed keeps one HTTP response open and sends events into it. API Gateway holds a response back until it is finished, so events would arrive late or not at all. That is why this app has its own hostname (`stream.windevopscloud.com`) straight on an ALB, not behind API Gateway. The app pings every 15 s and the ALB idle timeout is 300 s.
- SSE page uses `?access_token=` because EventSource cannot set headers; for production move to a cookie.
