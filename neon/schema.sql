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

create index if not exists games_batch_idx on games (batch_id);

-- Per model: seat-games in finished games, wins, win rate. A seat wins if its team won.
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
    where g.status = 'finished'
)
select model,
       count(*) as seat_games,
       count(*) filter (where won) as wins,
       round((count(*) filter (where won))::numeric / count(*), 3) as win_rate
from results
group by model;

-- Read-only access for the Data API's anonymous role (viewer, #40). Never grant writes.
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'anonymous') then
        grant usage on schema public to anonymous;
        grant select on games, events, labels, leaderboard to anonymous;
    end if;
end $$;
