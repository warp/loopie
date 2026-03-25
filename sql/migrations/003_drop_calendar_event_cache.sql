-- Remove calendar_event_cache if a prior 001_init.sql version created it.
DROP TABLE IF EXISTS calendar_event_cache;
