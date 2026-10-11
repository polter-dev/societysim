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
    -- Database clock at insert; `sync-neon --prune` uses it to tell a stalled game from a live
    -- one. Fresh databases only: an existing events table gets it from
    -- neon/migrations/001_events_inserted_at.sql (`sync-neon --migrate`), never from this file.
    inserted_at    timestamptz not null default now(),
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
-- The views look events up by game and type (game_start, vote, elimination, death_reveal) (#447).
create index if not exists events_game_type_idx on events (game_id, type);

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
where g.status = 'finished' and g.agents is distinct from 'scripted'
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
-- finished game reads in full. Any other (running, or failed, which batch resume reruns with the
-- same game_id and seed, so the same roles): only its public events, with private_reasoning and
-- suspicion set to null, so the live site can't spoil roles. meta.usage (token counts, cost,
-- latency, attempts, cache hit) and meta.ts go too (#387): wolves, the seer and the doctor get
-- longer prompts, so per-call sizes and timings would give their roles away. Keep this list in
-- step with LIVE_HIDDEN_META in logging/sinks/neon.py.
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
       case when g.status = 'finished'
            then e.event
            else ((e.event || jsonb_build_object('private_reasoning', null, 'suspicion', null))
                  #- '{meta,usage}' #- '{meta,ts}') end
           as event
from events e
join games g on g.game_id = e.game_id
where g.status = 'finished' or e.event ->> 'visibility' = 'public';

-- What anonymous reads instead of labels (#323): a finished game's labels only. Labels name
-- roles and lies, so a running or failed game (rerun with the same roles) shows none.
create or replace view labels_public with (security_barrier) as
select l.game_id,
       l.idx,
       l.label_type,
       l.actor,
       l.source_seq,
       l.source,
       l.label
from labels l
join games g on g.game_id = l.game_id
where g.status = 'finished';

-- Global leaderboard (#344): who spots wolves, who lies best. Batch and visitor games alike,
-- finished and not scripted, as for leaderboard. Metric definitions: docs/DATA_CONTRACT.md.

-- Internal (never granted to anonymous): one row per seat of a finished game, with whether the
-- seat was still alive at the end (never eliminated or found dead).
create or replace view finished_seats as
select g.game_id,
       s.key as seat,
       s.value ->> 'model' as model,
       s.value ->> 'role' = 'wolf' as wolf,
       not exists (select 1 from events d
                   where d.game_id = g.game_id and d.type in ('elimination', 'death_reveal')
                     and d.event ->> 'target' = s.key) as survived
from games g
join events e on e.game_id = g.game_id and e.type = 'game_start'
cross join lateral jsonb_each(e.event -> 'meta' -> 'seats') s
where g.status = 'finished' and g.agents is distinct from 'scripted';

-- Internal: each non-wolf seat's last suspicion of each day, one row per player it rated (the
-- pairs metrics.py's detection reads).
create or replace view finished_suspicion as
with last_of_day as (
    select distinct on (e.game_id, e.actor, e.day)
           e.game_id, e.actor, e.event -> 'suspicion' as suspicion
    from events e
    join finished_seats j on j.game_id = e.game_id and j.seat = e.actor and not j.wolf
    where jsonb_typeof(e.event -> 'suspicion') = 'array'
    order by e.game_id, e.actor, e.day, e.seq desc
)
select l.game_id,
       l.actor as judge,
       x ->> 'player' as subject,
       (x ->> 'p')::numeric as p
from last_of_day l
cross join lateral jsonb_array_elements(l.suspicion) x;

-- Best wolf detectors: per model, over its non-wolf seats. Materialized CTEs, as for
-- global_liars below (#447).
create or replace view global_detectors as
with seats as materialized (
    select game_id, seat, model, wolf from finished_seats
),
played as materialized (
    select model, count(*) as village_games from seats where not wolf group by model
),
judged as materialized (
    select j.model,
           avg(f.p) filter (where s.wolf) - avg(f.p) filter (where not s.wolf) as gap
    from finished_suspicion f
    join seats j on j.game_id = f.game_id and j.seat = f.judge
    join seats s on s.game_id = f.game_id and s.seat = f.subject
    group by j.model
),
voted as materialized (
    select v.model, count(*) as votes, count(*) filter (where t.wolf) as wolf_votes
    from events e
    join seats v on v.game_id = e.game_id and v.seat = e.actor and not v.wolf
    join seats t on t.game_id = e.game_id and t.seat = e.event ->> 'target'
    where e.type = 'vote'
    group by v.model
)
select p.model,
       p.village_games,
       round(j.gap, 3) as suspicion_gap,
       coalesce(v.votes, 0) as votes,
       round(v.wolf_votes::numeric / nullif(v.votes, 0), 3) as wolf_vote_rate,
       p.village_games >= 5 as ranked
from played p
left join judged j using (model)
left join voted v using (model)
where p.model <> 'human';

-- Best liars: per model, over its wolf seats. Lie success follows metrics.py share_followed.
-- Each step is a materialized CTE so it runs once (#447): inlined, the planner misjudged the
-- jsonb row counts and re-ran whole aggregates per model, past the Data API's statement timeout.
create or replace view global_liars as
with seats as materialized (
    select * from finished_seats
),
played as materialized (
    select model, count(*) as wolf_games, count(*) filter (where survived) as survived
    from seats where wolf group by model
),
suspected as materialized (
    select w.model, avg(f.p) as p
    from finished_suspicion f
    join seats w on w.game_id = f.game_id and w.seat = f.subject and w.wolf
    group by w.model
),
voted as materialized (
    select w.model, count(*) as votes
    from events e
    join seats w on w.game_id = e.game_id and w.seat = e.event ->> 'target' and w.wolf
    join seats v on v.game_id = e.game_id and v.seat = e.actor and not v.wolf
    where e.type = 'vote'
    group by w.model
),
told as materialized (
    select l.game_id, l.source, l.label_type, l.actor, l.source_seq, w.model,
           src.day, src.type as src_type,
           src.event ->> 'target' as src_target,
           src.event -> 'meta' ->> 'result' as result
    from labels l
    join seats w on w.game_id = l.game_id and w.seat = l.actor and w.wolf
    join events src on src.game_id = l.game_id and src.seq = l.source_seq
    where l.source in ('self_tag', 'jev')
      and l.label_type in ('false_role_claim', 'false_report', 'bussing')
),
heard as (
    select t.model,
           t.source,
           case
               when t.label_type = 'bussing' then
                   case when sw.wolf then e.event ->> 'target' = t.src_target
                        else vt.wolf and e.event ->> 'target' <> t.actor end
               when t.label_type = 'false_report' and t.src_type = 'seer_claim' then
                   case when t.result = 'wolf' then e.event ->> 'target' = t.src_target
                        else e.event ->> 'target' not in (t.src_target, t.actor) end
               else e.event ->> 'target' <> t.actor
           end as followed
    from told t
    join events e on e.game_id = t.game_id and e.type = 'vote' and e.day = t.day
                 and e.seq > t.source_seq
    join seats v on v.game_id = e.game_id and v.seat = e.actor and not v.wolf
    left join seats vt on vt.game_id = e.game_id and vt.seat = e.event ->> 'target'
    left join seats sw on sw.game_id = t.game_id and sw.seat = t.src_target
),
fooled as materialized (
    select model,
           count(*) filter (where source = 'self_tag') as self_tag_voters,
           count(*) filter (where source = 'self_tag' and followed) as self_tag_followed,
           count(*) filter (where source = 'jev') as jev_voters,
           count(*) filter (where source = 'jev' and followed) as jev_followed
    from heard
    group by model
)
select p.model,
       p.wolf_games,
       round(s.p, 3) as suspicion_received,
       round(p.survived::numeric / p.wolf_games, 3) as survival_rate,
       round(coalesce(v.votes, 0)::numeric / p.wolf_games, 3) as votes_received_per_game,
       coalesce(f.self_tag_voters, 0) as self_tag_lie_voters,
       round(f.self_tag_followed::numeric / nullif(f.self_tag_voters, 0), 3) as self_tag_lie_success,
       coalesce(f.jev_voters, 0) as jev_lie_voters,
       round(f.jev_followed::numeric / nullif(f.jev_voters, 0), 3) as jev_lie_success,
       p.wolf_games >= 5 as ranked
from played p
left join suspected s using (model)
left join voted v using (model)
left join fooled f using (model)
where p.model <> 'human';

-- Read-only access for the Data API's anonymous role (viewer, #40). Never grant writes.
-- No direct select on events or labels (#323): anonymous reads events_public and labels_public.
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'anonymous') then
        grant usage on schema public to anonymous;
        revoke all on events, labels from anonymous;
        grant select on games, events_public, labels_public, leaderboard to anonymous;
        grant select on leaderboard_by_batch, lies_by_model to anonymous;
        grant select on global_detectors, global_liars to anonymous;
        revoke all on visitor_spend from anonymous;
        revoke all on finished_seats, finished_suspicion from anonymous;  -- internal (#344)
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        revoke all on events, labels from authenticated;  -- unused by us; no way around the views
    end if;
end $$;

-- Ask the Data API (PostgREST) to reload its schema cache so the new views show up at once.
notify pgrst, 'reload schema';
