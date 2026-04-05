-- Link notes to Google Calendar events (event_id from calendar_list_events / create responses).

ALTER TABLE notes
    ADD COLUMN IF NOT EXISTS calendar_event_id TEXT;

CREATE INDEX IF NOT EXISTS idx_notes_user_calendar_event
    ON notes (user_id, calendar_event_id)
    WHERE calendar_event_id IS NOT NULL;
