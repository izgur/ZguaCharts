CREATE TABLE users (
 id BIGINT PRIMARY KEY, name TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, hash TEXT NOT NULL,
 first_name TEXT, last_name TEXT, email TEXT, email_verified INTEGER NOT NULL DEFAULT 1,
 avatar TEXT, role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('member','tactics','admin')),
 disabled INTEGER NOT NULL DEFAULT 0, totp_secret TEXT, totp_pending TEXT,
 totp_pending_until DOUBLE PRECISION, totp_last BIGINT NOT NULL DEFAULT -1, recovery TEXT
);
CREATE UNIQUE INDEX users_email ON users(lower(email)) WHERE email IS NOT NULL;
CREATE TABLE account_sequence(singleton INTEGER PRIMARY KEY, next_id BIGINT NOT NULL);
INSERT INTO account_sequence VALUES(1,1);
CREATE TABLE sessions(token TEXT PRIMARY KEY, user_id BIGINT REFERENCES users(id) ON DELETE CASCADE, csrf TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL);
CREATE INDEX sessions_expiry ON sessions(expires);
CREATE TABLE profiles(user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, body TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0);
CREATE TABLE attempts(ip TEXT NOT NULL, created DOUBLE PRECISION NOT NULL);
CREATE INDEX attempts_ip_time ON attempts(ip,created);
CREATE TABLE email_tokens(token TEXT PRIMARY KEY,user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,expires DOUBLE PRECISION NOT NULL);
CREATE TABLE account_tokens(token TEXT PRIMARY KEY,user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,purpose TEXT NOT NULL,address TEXT,expires DOUBLE PRECISION NOT NULL);
CREATE TABLE admin_audit(id BIGSERIAL PRIMARY KEY,actor BIGINT,action TEXT NOT NULL,target BIGINT,created DOUBLE PRECISION NOT NULL);
CREATE TABLE site_settings(name TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE user_documents(user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,kind TEXT NOT NULL,document_key TEXT NOT NULL,body TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,updated DOUBLE PRECISION NOT NULL,PRIMARY KEY(user_id,kind,document_key));
CREATE TABLE user_document_versions(user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,kind TEXT NOT NULL,document_key TEXT NOT NULL,revision INTEGER NOT NULL,body TEXT NOT NULL,created DOUBLE PRECISION NOT NULL,PRIMARY KEY(user_id,kind,document_key,revision));
CREATE TABLE alert_records(id TEXT PRIMARY KEY,user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,body TEXT NOT NULL,revision INTEGER NOT NULL,updated DOUBLE PRECISION NOT NULL);
CREATE INDEX alert_records_owner ON alert_records(user_id);
CREATE TABLE alert_events(id TEXT PRIMARY KEY,user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,alert_id TEXT NOT NULL,body TEXT NOT NULL,created DOUBLE PRECISION NOT NULL);
CREATE INDEX alert_events_owner_time ON alert_events(user_id,created);
CREATE TABLE mail_usage(period TEXT PRIMARY KEY,attempts INTEGER NOT NULL);
CREATE TABLE mail_deliveries(id TEXT PRIMARY KEY,recipient TEXT NOT NULL,subject TEXT NOT NULL,status TEXT NOT NULL,provider_id TEXT,error TEXT,attempts INTEGER NOT NULL,created DOUBLE PRECISION NOT NULL,updated DOUBLE PRECISION NOT NULL);
CREATE TABLE backtest_runs(id TEXT PRIMARY KEY,user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,body TEXT NOT NULL,created DOUBLE PRECISION NOT NULL);
CREATE INDEX backtest_runs_owner_time ON backtest_runs(user_id,created);
