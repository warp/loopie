-- Notes in AlloyDB (PostgreSQL-compatible).
-- Run against your database before using the agent tools.

CREATE TABLE IF NOT EXISTS notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    tags TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_notes_user ON notes (user_id);
CREATE INDEX IF NOT EXISTS idx_notes_tags ON notes USING GIN (tags);
