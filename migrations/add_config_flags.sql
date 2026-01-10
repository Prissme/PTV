-- Créer table pour flags de config
CREATE TABLE IF NOT EXISTS config_flags (
    flag_name TEXT PRIMARY KEY,
    value BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Index pour perfs
CREATE INDEX IF NOT EXISTS idx_config_flags_name ON config_flags(flag_name);
