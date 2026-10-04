-- Lastly Family Hub. The complete extraction lives in JSONB; family edits have
-- their own columns so a fresh analysis can preserve them.
CREATE TABLE IF NOT EXISTS estates (
    id SERIAL PRIMARY KEY,
    persona JSONB NOT NULL,
    stats JSONB NOT NULL,
    totals JSONB NOT NULL,
    today DATE NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Compatible with the original build-plan schema, if it was created manually.
ALTER TABLE estates ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS accounts (
    estate_id INTEGER NOT NULL REFERENCES estates(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    data JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'in_progress', 'done')),
    assigned_to TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (estate_id, id)
);

CREATE TABLE IF NOT EXISTS activity (
    id SERIAL PRIMARY KEY,
    estate_id INTEGER NOT NULL REFERENCES estates(id) ON DELETE CASCADE,
    account_id TEXT,
    actor TEXT,
    action TEXT,
    sync_key TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE activity ADD COLUMN IF NOT EXISTS sync_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS activity_sync_key_idx ON activity (sync_key);

CREATE INDEX IF NOT EXISTS estates_latest_idx ON estates (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS activity_estate_latest_idx ON activity (estate_id, created_at DESC, id DESC);
