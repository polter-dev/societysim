-- Adds the database-side insert time that `societysim sync-neon --prune` needs.
-- Idempotent. Existing rows get the time this runs, which only makes old games look newer
-- (so prune is more cautious, never less). Needs a human OK before running on the live database.
-- events_public is deliberately not changed: it lists its columns explicitly and stays as is.
alter table events add column if not exists inserted_at timestamptz not null default now();
