#!/bin/bash
# Runs on the tools EC2 (has psql and the IAM role). Creates or syncs the streamnotify database, the
# streaming_app user (password from the streamnotify/poc/rds-app secret) and the tables. Safe to repeat.
set -euo pipefail
export HOME=/root
REGION="${AWS_REGION:-us-west-2}"
APP_SECRET="${APP_SECRET:-streamnotify/poc/rds-app}"

get() { aws secretsmanager get-secret-value --region "$REGION" --secret-id "$1" --query SecretString --output text; }
j() { echo "$1" | python3 -c "import sys,json; print(json.load(sys.stdin)['$2'])"; }

APP_JSON=$(get "$APP_SECRET")
HOST=$(j "$APP_JSON" host); PORT=$(j "$APP_JSON" port); DB=$(j "$APP_JSON" dbname)
APP_USER=$(j "$APP_JSON" username); APP_PW=$(j "$APP_JSON" password)
MASTER_JSON=$(get "$(j "$APP_JSON" masterarn)")
export PGPASSWORD=$(j "$MASTER_JSON" password)
MASTER_USER=$(j "$MASTER_JSON" username)

echo "Syncing database ${DB} and user ${APP_USER} on ${HOST}"
psql "host=${HOST} port=${PORT} dbname=postgres user=${MASTER_USER} sslmode=require" \
  -v ON_ERROR_STOP=1 -v app_user="$APP_USER" -v app_pw="$APP_PW" -v dbname="$DB" \
  -f db/01_create_db_user.sql

psql "host=${HOST} port=${PORT} dbname=${DB} user=${MASTER_USER} sslmode=require" \
  -v ON_ERROR_STOP=1 -f db/02_schema.sql
echo "Database ready"
