-- Neon Postgres schema. Idempotent: safe to run on every connect.
-- JSONL stays the source of truth; these tables mirror it. The full event is stored as jsonb,
-- so a schema_version bump needs no migration.

create table if not exists games (
    game_id    text primary key,
    batch_id   text,
    status     text not null default 'running' check (status in ('running', 'finished', 'failed')),
    winner     text,
    models     jsonb,
    started_at timestamptz,
    ended_at   timestamptz
);

create table if not exists events (
    game_id        text not null references games (game_id) on delete cascade,
    seq            integer not null,
    day            integer not null,
    phase          text not null,
    type           text not null,
    actor          text,
    schema_version integer not null,
    event          jsonb not null,
    primary key (game_id, seq)
);

-- One row per line of <game_id>.labels.jsonl; idx is the line index.
create table if not exists labels (
    game_id    text not null references games (game_id) on delete cascade,
    idx        integer not null,
    label_type text not null,
    actor      text not null,
    source_seq integer not null,
    source     text not null,
    label      jsonb not null,
    primary key (game_id, idx)
);

-- Agent type of the game: 'scripted' | 'llm' (run_meta.json "agents"). Null = legacy or unknown
-- (rows written before this column, or by older code); the views keep null rows and drop 'scripted'.
alter table games add column if not exists agents text;

create index if not exists games_batch_idx on games (batch_id);

-- Per model: seat-games in finished games, wins, win rate. A seat wins if its team won.
-- Pools every batch and leaves out human seats and scripted games; use leaderboard_by_batch to compare fairly.
create or replace view leaderboard as
with seats as (
    select e.game_id,
           s.value ->> 'model' as model,
           s.value ->> 'role' as role
    from events e, jsonb_each(e.event -> 'meta' -> 'seats') s
    where e.type = 'game_start'
),
results as (
    select s.model,
           (s.role = 'wolf' and g.winner = 'wolves')
               or (s.role <> 'wolf' and g.winner = 'villagers') as won
    from seats s
    join games g using (game_id)
    where g.status = 'finished' and g.agents is distinct from 'scripted' and s.model <> 'human'
)
select model,
       count(*) as seat_games,
       count(*) filter (where won) as wins,
       round((count(*) filter (where won))::numeric / count(*), 3) as win_rate
from results
group by model;

-- Same, per batch, with draws. A draw counts as not won (matches analysis/metrics.py).
create or replace view leaderboard_by_batch as
select g.batch_id,
       s.value ->> 'model' as model,
       count(*) as seat_games,
       count(*) filter (where (s.value ->> 'role' = 'wolf' and g.winner = 'wolves')
                           or (s.value ->> 'role' <> 'wolf' and g.winner = 'villagers')) as wins,
       count(*) filter (where g.winner = 'draw') as draws,
       round((count(*) filter (where (s.value ->> 'role' = 'wolf' and g.winner = 'wolves')
                                  or (s.value ->> 'role' <> 'wolf' and g.winner = 'villagers')))::numeric
             / count(*), 3) as win_rate
from games g
join events e on e.game_id = g.game_id and e.type = 'game_start'
cross join lateral jsonb_each(e.event -> 'meta' -> 'seats') s
where g.status = 'finished' and g.agents is distinct from 'scripted'
  and s.value ->> 'model' <> 'human'
group by g.batch_id, s.value ->> 'model';

-- Lies per liar model, from self_tag and jev labels (the four types metrics.py counts).
create or replace view lies_by_model as
select g.batch_id,
       seat.value ->> 'model' as model,
       l.source,
       l.label_type,
       count(*) as lies
from labels l
join games g on g.game_id = l.game_id
join events e on e.game_id = l.game_id and e.type = 'game_start'
cross join lateral (select value from jsonb_each(e.event -> 'meta' -> 'seats') where key = l.actor) seat
where g.agents is distinct from 'scripted'
  and l.source in ('self_tag', 'jev')
  and l.label_type in ('false_role_claim', 'false_report', 'bussing', 'hidden_knowledge')
group by g.batch_id, seat.value ->> 'model', l.source, l.label_type;

-- Visitor play spend (#281): one row per booked model call, plus a row per game for anything
-- the per-call metering missed. The server sums today's rows at startup to enforce
-- PLAY_DAILY_BUDGET_USD. Server-only: never granted to anonymous, and row-level security with
-- no policies hides it from any non-owner role that gets a grant by mistake.
create table if not exists visitor_spend (
    day        date not null,
    game_id    text not null,
    cost_usd   numeric not null,
    created_at timestamptz not null default now()
);
create index if not exists visitor_spend_day_idx on visitor_spend (day);
alter table visitor_spend enable row level security;

-- What the Data API's anonymous role reads instead of events (#323), with the same columns. A
-- game that is over (finished or failed) reads in full. While a game runs: only its public
-- events, with private_reasoning and suspicion set to null, so the live site can't spoil roles.
-- The view runs with its owner's (the writer's) rights; security_barrier stops a caller's
-- filters from being evaluated on hidden rows first.
create or replace view events_public with (security_barrier) as
select e.game_id,
       e.seq,
       e.day,
       e.phase,
       e.type,
       e.actor,
       e.schema_version,
       case when g.status = 'running'
            then e.event || '{"private_reasoning": null, "suspicion": null}'::jsonb
            else e.event end as event
from events e
join games g on g.game_id = e.game_id
where g.status <> 'running' or e.event ->> 'visibility' = 'public';

-- Read-only access for the Data API's anonymous role (viewer, #40). Never grant writes.
-- No direct select on events (#323): anonymous reads events_public. Labels are written only
-- after a game is over (sync_game sets the status first), so labels stay granted.
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'anonymous') then
        grant usage on schema public to anonymous;
        revoke all on events from anonymous;
        grant select on games, events_public, labels, leaderboard to anonymous;
        grant select on leaderboard_by_batch, lies_by_model to anonymous;
        revoke all on visitor_spend from anonymous;
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        revoke all on events from authenticated;  -- we don't use it; no way around events_public
    end if;
end $$;

-- Ask the Data API (PostgREST) to reload its schema cache so events_public shows up at once.
notify pgrst, 'reload schema';
