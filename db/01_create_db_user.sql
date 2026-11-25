-- Run once as the RDS admin, connected to the default "postgres" database.
-- The password is passed on the command line, so it is never stored in a file:
--   PW=$(aws secretsmanager get-secret-value --secret-id streamnotify/poc/rds-app --query SecretString --output text | jq -r .password)
--   psql "host=<endpoint> dbname=postgres user=<admin> sslmode=require" -v app_pw="$PW" -f db/01_create_db_user.sql
-- (The secret streamnotify/poc/rds-app must hold {"username":"streaming_app","password":"<new password>"} first.)
CREATE USER streaming_app WITH PASSWORD 'streaming@123';
GRANT CONNECT ON DATABASE streamnotify TO streaming_app;
REVOKE ALL ON DATABASE streamnotify FROM PUBLIC;
