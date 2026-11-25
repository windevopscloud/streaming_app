"""Tiny DB layer: SQLite for local/test; PostgreSQL (RDS) when DB_HOST is set (signup_app style) or DATABASE_URL is postgres://."""
import contextlib
import sqlite3

from . import config

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        phone TEXT NOT NULL,
        email_verified INTEGER NOT NULL DEFAULT 0,
        phone_verified INTEGER NOT NULL DEFAULT 0,
        totp_secret TEXT,
        totp_enabled INTEGER NOT NULL DEFAULT 0,
        totp_last_step BIGINT NOT NULL DEFAULT 0,
        created BIGINT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS otps (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        channel TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        expires BIGINT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        used INTEGER NOT NULL DEFAULT 0,
        created BIGINT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS otps_user ON otps (user_id, channel, created)",
    """CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        stage TEXT NOT NULL,
        expires BIGINT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS recovery_codes (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        used INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS subscriptions (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        topic TEXT NOT NULL,
        status TEXT NOT NULL,
        last_sent BIGINT NOT NULL DEFAULT 0,
        created BIGINT NOT NULL,
        expires BIGINT NOT NULL DEFAULT 0,
        UNIQUE (email, topic))""",
    """CREATE TABLE IF NOT EXISTS plans (
        topic TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        price_cents INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS test_cards (
        number TEXT PRIMARY KEY,
        expiry TEXT NOT NULL,
        cvv TEXT NOT NULL,
        balance_cents INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS payments (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        topic TEXT NOT NULL,
        card_last4 TEXT,
        amount_cents INTEGER NOT NULL,
        status TEXT NOT NULL,
        reason TEXT NOT NULL,
        created BIGINT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS events (
        seq BIGINT PRIMARY KEY,
        topic TEXT NOT NULL,
        message TEXT NOT NULL,
        created BIGINT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS outbox (
        id TEXT PRIMARY KEY,
        channel TEXT NOT NULL,
        recipient TEXT NOT NULL,
        subject TEXT,
        body TEXT NOT NULL,
        created BIGINT NOT NULL)""",
]


class DB:
    def __init__(self, url, use_params=False):
        self.url = url
        self.use_params = use_params
        self.pg = use_params or url.startswith(("postgres://", "postgresql://"))
        self.path = None if self.pg else (url.split("///", 1)[1] if "///" in url else url)

    def _pg_connect(self):
        import psycopg2
        import psycopg2.extras
        if self.use_params:   # login read from the mounted secret files on every connection
            return psycopg2.connect(
                host=config.DB_HOST, port=config.DB_PORT, dbname=config.DB_NAME,
                user=config._secret("DB_USER"), password=config._secret("DB_PASSWORD"),
                sslmode=config.DB_SSLMODE, connect_timeout=5,
                cursor_factory=psycopg2.extras.RealDictCursor)
        return psycopg2.connect(self.url, cursor_factory=psycopg2.extras.RealDictCursor)

    @contextlib.contextmanager
    def _conn(self):
        if self.pg:
            c = self._pg_connect()
        else:
            c = sqlite3.connect(self.path, timeout=10)
            c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def _run(self, sql, args):
        if self.pg:
            sql = sql.replace("?", "%s")
        with self._conn() as c:
            cur = c.cursor()
            cur.execute(sql, tuple(args))
            rows = [dict(r) for r in cur.fetchall()] if cur.description else []
            return rows, cur.rowcount

    def q(self, sql, args=()):
        return self._run(sql, args)[0]

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql, args=()):
        return self._run(sql, args)[1]

    def init(self):
        if not config.DB_INIT:
            return
        for stmt in SCHEMA:
            self.x(stmt)
        # migration for databases created before paid plans existed
        try:
            self.x("ALTER TABLE subscriptions ADD COLUMN expires BIGINT NOT NULL DEFAULT 0")
        except Exception:
            pass


db = DB(config.DATABASE_URL, use_params=bool(config.DB_HOST))
