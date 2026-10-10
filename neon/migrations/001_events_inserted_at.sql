-- Adds the database-side insert time that `societysim sync-neon --prune` needs.
-- Idempotent. Existing rows get the time this runs, which only makes old games look newer
-- (so prune is more cautious, never less). Needs a human OK before running on the live database.
alter table events add column if not exists inserted_at timestamptz not null default now();

-- events_public lists the same columns as events (#323), so it gets the new one, last (which keeps
-- create or replace valid). Identical to the view in neon/schema.sql; a test checks they match.
create or replace view events_public with (security_barrier) as
select e.game_id,
       e.seq,
       e.day,
       e.phase,
       e.type,
       e.actor,
       e.schema_version,
       case when g.status = 'finished'
            then e.event
            else e.event || jsonb_build_object('private_reasoning', null, 'suspicion', null) end
           as event,
       e.inserted_at
from events e
join games g on g.game_id = e.game_id
where g.status = 'finished' or e.event ->> 'visibility' = 'public';
