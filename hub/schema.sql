-- Dedicated tables only. Never modify legacy servers/global_config.
CREATE TABLE IF NOT EXISTS ip_machines (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    panel_origin TEXT NOT NULL,
    credentials TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    updated_at INTEGER NOT NULL,
    resources TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS ip_enrollments (
    code_hash TEXT PRIMARY KEY,
    machine_id TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
