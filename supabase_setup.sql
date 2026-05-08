-- Run this ONCE in the Supabase SQL editor:
-- https://app.supabase.com → your project → SQL Editor → New query

CREATE TABLE IF NOT EXISTS clipboard_rooms (
    room_code   TEXT        PRIMARY KEY,
    content     TEXT        NOT NULL DEFAULT '',
    sender      TEXT        NOT NULL DEFAULT '',   -- human-readable label
    device_id   TEXT        NOT NULL DEFAULT '',   -- UUID, prevents echo
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Row Level Security: the room code itself is the shared secret
ALTER TABLE clipboard_rooms ENABLE ROW LEVEL SECURITY;

CREATE POLICY "public_access" ON clipboard_rooms
    FOR ALL
    USING (true)
    WITH CHECK (true);

-- If you already created the table without device_id, run just this line:
-- ALTER TABLE clipboard_rooms ADD COLUMN IF NOT EXISTS device_id TEXT NOT NULL DEFAULT '';

-- NOTE: Realtime Broadcast (used by the web app) is channel-based — no table
-- configuration needed. Just make sure Realtime is enabled in your Supabase
-- project (it is by default for all new projects).
