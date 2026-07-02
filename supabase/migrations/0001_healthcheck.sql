-- Phase 0: trivial table to prove FastAPI <-> Supabase connectivity, with RLS on.
create table healthcheck (
  id bigint generated always as identity primary key,
  note text not null default 'ok',
  created_at timestamptz not null default now()
);

alter table healthcheck enable row level security;

-- Read-only, non-sensitive: allow anon/authenticated to select so /health can
-- use the anon key. No insert/update/delete policy is defined, so writes are
-- only possible with the service role key.
create policy "healthcheck_select" on healthcheck
  for select
  using (true);

insert into healthcheck (note) values ('ok');
