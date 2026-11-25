# StreamNotify

Email OTP + authenticator-app 2FA (mobile SMS OTP is optional, off by default: `SMS_ENABLED=1`) + double opt-in email subscriptions + live feed (SSE).
Starlette service, SQLite locally / PostgreSQL (RDS, own database + user) in the cluster, email over SMTP (same Gmail secret as signup_app) or SES; SNS for SMS only when `SMS_ENABLED=1`.

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

## HTTPS end to end
Browser -> ALB: HTTPS with the ACM certificate for `streaming.windevopscloud.com` (HTTP 80 redirects to 443).
ALB -> pod: HTTPS too (`backend-protocol: HTTPS`). The pod serves TLS itself on 8443 (`python -m app.serve`) with a self-signed certificate made at start (the ALB does not validate it). Probes, Service (443) and the test publisher use HTTPS.
signup_app is different: it ends TLS at the ALB and talks plain HTTP to its pods. Local development stays plain HTTP (`uvicorn app.main:app --port 8000`).

## Database (RDS PostgreSQL, same way as signup_app)
Own database `streamnotify` and own limited user `streaming_app` on the existing RDS instance. The app user can read/write rows only; it cannot create tables.
1. Put the login in Secrets Manager (value set by hand, never in Terraform): secret `streamnotify/poc/rds-app` = `{"username":"streaming_app","password":"<new password>"}`.
2. As the RDS admin (from the tools EC2, which can reach RDS):
   - `psql "host=<endpoint> dbname=postgres user=<admin> sslmode=require" -v app_pw="<same password>" -f db/01_create_db_user.sql`
   - `psql "host=<endpoint> dbname=streamnotify user=<admin> sslmode=require" -f db/02_schema.sql`
3. The pod reads `DB_USER_FILE` / `DB_PASSWORD_FILE` (mounted from the secret) and `DB_HOST` / `DB_NAME` / `DB_SSLMODE` from the config map. `DB_HOST` is the RDS endpoint from SSM `/rds/signup-poc/endpoint`, added at deploy time. With `DB_HOST` set the app does not run DDL (`DB_INIT=0`).
4. Without `DB_HOST` (local, tests) the app uses SQLite and creates its own tables. `db/02_schema.sql` is checked against `app/db.py` by a test.
After a rebuild of the account the database is new: repeat steps 1-2.

## Deploy (EKS, same pattern as signup_app)
Layout: `k8s/base` + `k8s/overlays/poc` (kustomize, no placeholders), `deploy/apply.sh`, `.github/workflows/build-push.yml`.
1. **Runner**: this repo has its own runner `streaming_app-runner-poc` (init_aws workflow: `create_app_runner=yes`, `APP_GITHUB_REPO=streaming_app`).
2. **Secrets Manager** (infra_aws): the pod reads `ses/poc/smtp` (Gmail login, already used by signup_app), `streamnotify/poc/rds-app` (DB login) and `streamnotify/poc/app` (`SECRET_KEY`, `PUBLISH_KEY`). The IAM role `streamnotify-role` must be allowed to read all three, or the pod stays in ContainerCreating.
3. **Host + TLS**: ACM certificate for `streaming.windevopscloud.com` in us-west-2 plus a CNAME to the ALB. The Ingress has no certificate ARN: the AWS Load Balancer Controller finds the certificate that matches the host.
4. **Build + deploy**: run the `build-push` workflow (`deploy=yes`). `deploy/apply.sh <tag> <region> <rds-endpoint>` writes a small kustomize overlay for that run (image = your account's ECR + the commit tag, IRSA role ARN, DB_HOST) and applies it. Replicas stay at 1 (in-memory SSE broker).
5. Test publisher: suspended CronJob. Run once: `kubectl create job --from=cronjob/streamnotify-publisher test-1`.

## AWS gotchas
- **Email**: `MESSAGING=smtp` is the same logic as signup_app (`SMTP_HOST`, `SMTP_PORT`, `MAIL_FROM`, `SMTP_USER_FILE`, `SMTP_PASSWORD_FILE`, STARTTLS on 587, which the node security group allows). `MESSAGING=aws` (SES API, port 443) cannot be reached from nodes without internet.
- **SNS SMS** (only with `SMS_ENABLED=1`, `MESSAGING=aws`): new accounts can text only verified destination numbers. India (+91) needs DLT registration.
- The live feed keeps one HTTP response open and sends events into it. API Gateway holds a response back until it is finished, so events would arrive late or not at all. That is why this app has its own hostname (`streaming.windevopscloud.com`) straight on an ALB, not behind API Gateway. The app pings every 15 s and the ALB idle timeout is 300 s.
- SSE page uses `?access_token=` because EventSource cannot set headers; for production move to a cookie.
