-- Migration P0.1 - Cooldowns persistants
-- Exécuter cette migration en premier

-- Table cooldowns pour persister les cooldowns critiques
CREATE TABLE IF NOT EXISTS cooldowns (
    user_id BIGINT NOT NULL,
    cooldown_type VARCHAR(50) NOT NULL,
    last_used TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB DEFAULT '{}',
    PRIMARY KEY (user_id, cooldown_type)
);

-- Index pour optimiser les requêtes
CREATE INDEX IF NOT EXISTS idx_cooldowns_user_type ON cooldowns(user_id, cooldown_type);
CREATE INDEX IF NOT EXISTS idx_cooldowns_expires ON cooldowns(expires_at);
CREATE INDEX IF NOT EXISTS idx_cooldowns_last_used ON cooldowns(last_used DESC);

-- Fonction pour nettoyer les cooldowns expirés automatiquement
CREATE OR REPLACE FUNCTION cleanup_expired_cooldowns() 
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM cooldowns WHERE expires_at < NOW();
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Extension de la table transaction_logs pour métadonnées
ALTER TABLE transaction_logs ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}';
ALTER TABLE transaction_logs ADD COLUMN IF NOT EXISTS transaction_id UUID DEFAULT gen_random_uuid();

-- Index pour optimiser les requêtes sur metadata
CREATE INDEX IF NOT EXISTS idx_transaction_logs_metadata ON transaction_logs USING GIN (metadata);
CREATE INDEX IF NOT EXISTS idx_transaction_logs_type_user ON transaction_logs(transaction_type, user_id);

-- Table pour les limites dynamiques (net worth cache)
CREATE TABLE IF NOT EXISTS user_net_worth_cache (
    user_id BIGINT PRIMARY KEY,
    main_balance BIGINT DEFAULT 0,
    bank_balance BIGINT DEFAULT 0,
    debt_balance BIGINT DEFAULT 0,
    net_worth BIGINT GENERATED ALWAYS AS (main_balance + bank_balance - debt_balance) STORED,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index pour optimiser les requêtes sur net worth
CREATE INDEX IF NOT EXISTS idx_net_worth_user ON user_net_worth_cache(user_id);
CREATE INDEX IF NOT EXISTS idx_net_worth_value ON user_net_worth_cache(net_worth DESC);

-- Table pour l'anti-spam des messages
CREATE TABLE IF NOT EXISTS message_spam_cache (
    user_id BIGINT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    PRIMARY KEY (user_id, content_hash)
);

-- Index pour nettoyer automatiquement les anciens hashes (> 1 heure)
CREATE INDEX IF NOT EXISTS idx_spam_cache_created ON message_spam_cache(created_at);

-- Fonction pour nettoyer le cache anti-spam
CREATE OR REPLACE FUNCTION cleanup_message_spam_cache() 
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM message_spam_cache WHERE created_at < NOW() - INTERVAL '1 hour';
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON TABLE cooldowns IS 'Cooldowns persistants pour éviter les bypass au redémarrage';
COMMENT ON TABLE user_net_worth_cache IS 'Cache du patrimoine net pour calculs de limites dynamiques';
COMMENT ON TABLE message_spam_cache IS 'Cache anti-spam pour éviter les messages dupliqués';
