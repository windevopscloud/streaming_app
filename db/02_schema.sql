-- Run by deploy/db-bootstrap.sh as the RDS admin, connected to the streamnotify database, right after
-- 01_create_db_user.sql (which creates the database and the streaming_app user). Safe to run again.
-- The app user (streaming_app) can only read and write rows: it cannot create or drop tables.
-- Tables created by the admin get the app user's privileges through ALTER DEFAULT PRIVILEGES.
GRANT USAGE ON SCHEMA public TO streaming_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO streaming_app;

CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        phone TEXT NOT NULL,
        email_verified INTEGER NOT NULL DEFAULT 0,
        phone_verified INTEGER NOT NULL DEFAULT 0,
        totp_secret TEXT,
        totp_enabled INTEGER NOT NULL DEFAULT 0,
        totp_last_step BIGINT NOT NULL DEFAULT 0,
        created BIGINT NOT NULL);

CREATE TABLE IF NOT EXISTS otps (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        channel TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        expires BIGINT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        used INTEGER NOT NULL DEFAULT 0,
        created BIGINT NOT NULL);

CREATE INDEX IF NOT EXISTS otps_user ON otps (user_id, channel, created);

CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        stage TEXT NOT NULL,
        expires BIGINT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS recovery_codes (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        used INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS subscriptions (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        topic TEXT NOT NULL,
        status TEXT NOT NULL,
        last_sent BIGINT NOT NULL DEFAULT 0,
        created BIGINT NOT NULL,
        expires BIGINT NOT NULL DEFAULT 0,
        UNIQUE (email, topic));

CREATE TABLE IF NOT EXISTS sub_history (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        topic TEXT NOT NULL,
        price_cents INTEGER NOT NULL,
        subscribed BIGINT NOT NULL,
        unsubscribed BIGINT NOT NULL DEFAULT 0);

CREATE INDEX IF NOT EXISTS sub_history_email ON sub_history (email, subscribed);

CREATE TABLE IF NOT EXISTS plans (
        topic TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        price_cents INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS test_cards (
        number TEXT PRIMARY KEY,
        expiry TEXT NOT NULL,
        cvv TEXT NOT NULL,
        balance_cents INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS payments (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        topic TEXT NOT NULL,
        card_last4 TEXT,
        amount_cents INTEGER NOT NULL,
        status TEXT NOT NULL,
        reason TEXT NOT NULL,
        created BIGINT NOT NULL);

CREATE TABLE IF NOT EXISTS events (
        seq BIGINT PRIMARY KEY,
        topic TEXT NOT NULL,
        message TEXT NOT NULL,
        created BIGINT NOT NULL);

CREATE TABLE IF NOT EXISTS outbox (
        id TEXT PRIMARY KEY,
        channel TEXT NOT NULL,
        recipient TEXT NOT NULL,
        subject TEXT,
        body TEXT NOT NULL,
        created BIGINT NOT NULL);

-- Safety net if the tables were created before the default privileges existed:
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO streaming_app;
