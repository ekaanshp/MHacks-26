-- Lastly Family Hub. The complete extraction lives in JSONB for the app; readable
-- columns come first so the Neon console shows people, companies and changes plainly.
-- Readable columns are derived automatically and are never edited by hand.

CREATE TABLE IF NOT EXISTS family_members (
    id TEXT PRIMARY KEY,                -- private mem_... id, backend only
    name TEXT NOT NULL,                 -- shown in the app
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS family_members_name_idx ON family_members (lower(name));

CREATE TABLE IF NOT EXISTS estates (
    id SERIAL PRIMARY KEY,
    person_name TEXT GENERATED ALWAYS AS (persona->>'name') STORED,
    date_of_death TEXT GENERATED ALWAYS AS (persona->>'date_of_death') STORED,
    person_city TEXT GENERATED ALWAYS AS (persona->>'city') STORED,
    person_email TEXT GENERATED ALWAYS AS (persona->>'email') STORED,
    accounts_found INTEGER GENERATED ALWAYS AS ((totals->>'accounts')::integer) STORED,
    monthly_charges NUMERIC GENERATED ALWAYS AS ((totals->>'monthly_drain')::numeric) STORED,
    assets_found NUMERIC GENERATED ALWAYS AS ((totals->>'assets_found')::numeric) STORED,
    debts_found NUMERIC GENERATED ALWAYS AS ((totals->>'debts_found')::numeric) STORED,
    today DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    persona JSONB NOT NULL,
    stats JSONB NOT NULL,
    totals JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS accounts (
    estate_id INTEGER NOT NULL REFERENCES estates(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    person_name TEXT,
    institution TEXT GENERATED ALWAYS AS (data->>'institution') STORED,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'in_progress', 'done')),
    assigned_to TEXT,
    assigned_member_id TEXT REFERENCES family_members(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    data JSONB NOT NULL,
    PRIMARY KEY (estate_id, id)
);

CREATE TABLE IF NOT EXISTS activity (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    description TEXT,
    person_name TEXT,
    institution TEXT,
    actor TEXT,
    actor_member_id TEXT REFERENCES family_members(id),
    action TEXT,                         -- e.g. status:done, assigned:Sarah, claim:opened
    account_id TEXT,
    estate_id INTEGER NOT NULL REFERENCES estates(id) ON DELETE CASCADE,
    sync_key TEXT UNIQUE
);

-- Compatibility with databases created from earlier versions of this schema.
ALTER TABLE estates ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE estates ADD COLUMN IF NOT EXISTS person_name TEXT GENERATED ALWAYS AS (persona->>'name') STORED;
ALTER TABLE estates ADD COLUMN IF NOT EXISTS date_of_death TEXT GENERATED ALWAYS AS (persona->>'date_of_death') STORED;
ALTER TABLE estates ADD COLUMN IF NOT EXISTS person_city TEXT GENERATED ALWAYS AS (persona->>'city') STORED;
ALTER TABLE estates ADD COLUMN IF NOT EXISTS person_email TEXT GENERATED ALWAYS AS (persona->>'email') STORED;
ALTER TABLE estates ADD COLUMN IF NOT EXISTS accounts_found INTEGER GENERATED ALWAYS AS ((totals->>'accounts')::integer) STORED;
ALTER TABLE estates ADD COLUMN IF NOT EXISTS monthly_charges NUMERIC GENERATED ALWAYS AS ((totals->>'monthly_drain')::numeric) STORED;
ALTER TABLE estates ADD COLUMN IF NOT EXISTS assets_found NUMERIC GENERATED ALWAYS AS ((totals->>'assets_found')::numeric) STORED;
ALTER TABLE estates ADD COLUMN IF NOT EXISTS debts_found NUMERIC GENERATED ALWAYS AS ((totals->>'debts_found')::numeric) STORED;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS person_name TEXT;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS institution TEXT GENERATED ALWAYS AS (data->>'institution') STORED;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS assigned_member_id TEXT REFERENCES family_members(id);
ALTER TABLE activity ADD COLUMN IF NOT EXISTS sync_key TEXT;
ALTER TABLE activity ADD COLUMN IF NOT EXISTS actor_member_id TEXT REFERENCES family_members(id);
ALTER TABLE activity ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE activity ADD COLUMN IF NOT EXISTS person_name TEXT;
ALTER TABLE activity ADD COLUMN IF NOT EXISTS institution TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS activity_sync_key_idx ON activity (sync_key);
CREATE INDEX IF NOT EXISTS estates_latest_idx ON estates (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS activity_estate_latest_idx ON activity (estate_id, created_at DESC, id DESC);

-- Returns the member id for a person, creating the member on first use.
CREATE OR REPLACE FUNCTION lastly_member_id(person TEXT) RETURNS TEXT AS $$
DECLARE
    member TEXT;
BEGIN
    IF person IS NULL OR btrim(person) = '' THEN
        RETURN NULL;
    END IF;
    INSERT INTO family_members (id, name)
    VALUES ('mem_' || substr(replace(gen_random_uuid()::text, '-', ''), 1, 16), btrim(person))
    ON CONFLICT ((lower(name))) DO NOTHING;
    SELECT id INTO member FROM family_members WHERE lower(name) = lower(btrim(person));
    RETURN member;
END $$ LANGUAGE plpgsql;

-- Accounts: whose estate it belongs to and the assignee's private member id.
CREATE OR REPLACE FUNCTION lastly_link_assignee() RETURNS trigger AS $$
BEGIN
    NEW.assigned_member_id := lastly_member_id(NEW.assigned_to);
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION lastly_account_person() RETURNS trigger AS $$
BEGIN
    SELECT persona->>'name' INTO NEW.person_name FROM estates WHERE id = NEW.estate_id;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

-- Activity: the actor's member id (agents are not family members) and a plain-English description.
CREATE OR REPLACE FUNCTION lastly_link_actor() RETURNS trigger AS $$
BEGIN
    IF NEW.actor IS NULL OR NEW.actor ILIKE '%agent' THEN
        NEW.actor_member_id := NULL;
    ELSE
        NEW.actor_member_id := lastly_member_id(NEW.actor);
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION lastly_describe_activity() RETURNS trigger AS $$
DECLARE
    subject TEXT;
BEGIN
    SELECT persona->>'name' INTO NEW.person_name FROM estates WHERE id = NEW.estate_id;
    IF NEW.account_id IS NOT NULL THEN
        SELECT data->>'institution' INTO NEW.institution FROM accounts WHERE estate_id = NEW.estate_id AND id = NEW.account_id;
    END IF;
    subject := coalesce(NEW.institution, 'an account');
    NEW.description := coalesce(NEW.actor, 'Someone') || ' ' || CASE
        WHEN NEW.action = 'status:done' THEN 'marked ' || subject || ' as done'
        WHEN NEW.action = 'status:in_progress' THEN 'started working on ' || subject
        WHEN NEW.action = 'status:open' THEN 'reopened ' || subject
        WHEN NEW.action = 'assigned:Unassigned' THEN 'unassigned ' || subject
        WHEN NEW.action LIKE 'assigned:%' THEN 'assigned ' || subject || ' to ' || substr(NEW.action, 10)
        WHEN NEW.action = 'call:browser' THEN 'had the AI voice agent talk with ' || subject
        WHEN NEW.action = 'call:placed' THEN 'had the AI voice agent phone ' || subject
        WHEN NEW.action = 'claim:requested' THEN 'asked Lastly''s Fetch.ai agent to open a claim with ' || subject
        WHEN NEW.action = 'claim:opened' THEN 'opened a claim for ' || subject
        WHEN NEW.action = 'claim:rejected' THEN 'could not open a claim for ' || subject
        ELSE replace(coalesce(NEW.action, 'updated'), ':', ' ') || ' (' || subject || ')'
    END || coalesce(' — ' || NEW.person_name || '''s estate', '');
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS accounts_link_assignee ON accounts;
CREATE TRIGGER accounts_link_assignee BEFORE INSERT OR UPDATE OF assigned_to ON accounts
    FOR EACH ROW EXECUTE FUNCTION lastly_link_assignee();
DROP TRIGGER IF EXISTS accounts_person_name ON accounts;
CREATE TRIGGER accounts_person_name BEFORE INSERT ON accounts
    FOR EACH ROW EXECUTE FUNCTION lastly_account_person();
DROP TRIGGER IF EXISTS activity_link_actor ON activity;
CREATE TRIGGER activity_link_actor BEFORE INSERT OR UPDATE OF actor ON activity
    FOR EACH ROW EXECUTE FUNCTION lastly_link_actor();
DROP TRIGGER IF EXISTS activity_describe ON activity;
CREATE TRIGGER activity_describe BEFORE INSERT OR UPDATE OF action ON activity
    FOR EACH ROW EXECUTE FUNCTION lastly_describe_activity();

-- Fill readable columns for rows saved before they existed.
UPDATE accounts a SET person_name = e.persona->>'name'
FROM estates e WHERE e.id = a.estate_id AND a.person_name IS DISTINCT FROM e.persona->>'name';
UPDATE accounts SET assigned_to = assigned_to WHERE assigned_to IS NOT NULL AND assigned_member_id IS NULL;
UPDATE activity SET actor = actor WHERE actor_member_id IS NULL AND actor IS NOT NULL AND actor NOT ILIKE '%agent';
UPDATE activity SET action = action WHERE description IS NULL;

-- Readable views, filtered to the latest analysis where useful.
CREATE OR REPLACE VIEW account_overview AS
SELECT a.estate_id, a.person_name AS person, a.id AS account_id, a.institution,
       a.data->>'category' AS category, a.data->>'bucket' AS bucket,
       (a.data->>'amount')::numeric AS amount, a.data->>'frequency' AS frequency,
       a.status, a.assigned_to, a.assigned_member_id, a.updated_at,
       a.estate_id = (SELECT max(id) FROM estates) AS is_latest
FROM accounts a;

CREATE OR REPLACE VIEW activity_overview AS
SELECT id, created_at, description, actor, actor_member_id, action, institution, person_name, account_id, estate_id
FROM activity;
