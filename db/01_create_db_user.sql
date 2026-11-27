-- Run by deploy/db-bootstrap.sh as the RDS admin, connected to the default "postgres" database.
-- Variables (set by the script from the Secrets Manager secret streamnotify/poc/rds-app):
--   app_user, app_pw, dbname
-- Safe to run again: it creates what is missing and syncs the password with the secret.
SELECT format('CREATE ROLE %I LOGIN', :'app_user')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'app_user', :'app_pw') \gexec
SELECT format('CREATE DATABASE %I', :'dbname')
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'dbname') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'dbname') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'dbname', :'app_user') \gexec
