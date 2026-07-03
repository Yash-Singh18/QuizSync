 Context

 Phase 0 shipped a live walking skeleton (React static site → FastAPI → Supabase healthcheck).
 Phase 1 (per PLAN.md §"Phase 1" and DOCS/SystemDesign.md) makes a fully correct contest
 playable over the API with no Redis and no WebSockets — deliberately isolating the subtle
 scoring/idempotency bugs before realtime is layered on. This is where exactly-once scoring,
 absolute-deadline resume, and expiry handling get built and tested.

 Added scope from the user: host auth via Google OAuth through Supabase (participants stay
 anonymous). User handles the Google Cloud Console + Supabase provider config; we wire the code.

 Decisions locked with the user:
 - DB access: supabase-py client for CRUD + SECURITY DEFINER plpgsql functions via .rpc()
 for the atomic answer/sweep transactions (matches the design doc).
 - Host auth: backend uses the service-role key for all writes; verifies the Google OAuth
 JWT with supabase.auth.get_user() to get host_id, then enforces room.host_id == user.id
 in app code. RLS stays enabled as defense-in-depth.
 - Options model: per PLAN.md, use the system-design DDL — options as jsonb,
 correct_option_ids as a server-only jsonb column (never sent to the play client).
 (The normalized options table in RelationalModel.md §6 is the noted alternative; not used.)
 - Single-correct MCQ for the play UI (/answer { option_id } is singular); the column stays an
 array so multi-correct is a later, non-breaking extension.
 - room_results table is created now but finalize/persist is deferred to Phase 3;
 Phase 1 GET /results computes live standings straight from participants.

 Timer storage (Phase 1 stand-in for Redis)

 The design keeps started_at in Redis (HSETNX). With no Redis in Phase 1, add a
 question_starts(participant_id, question_id, started_at) table as the idempotent timer.
 start_question() does INSERT ... ON CONFLICT DO NOTHING then SELECT the canonical row —
 a direct 1:1 stand-in for HSETNX that Phase 2 swaps out for Redis. Absolute deadline =
 started_at + duration_ms, so refresh/reconnect resumes exactly.

 Migrations (supabase/migrations/)

 0002_core_schema.sql — enums + tables + indexes, straight from SystemDesign.md §5:
 - room_status, participant_status enums.
 - rooms, questions (jsonb options + server-only correct_option_ids), participants,
 submissions (UNIQUE (participant_id, question_id) = exactly-once), room_results.
 - question_starts (timer table above).
 - Partial unique index uniq_active_join_code on rooms(join_code) WHERE status IN ('ready','scheduled','live');
 idx_rooms_scheduler, idx_submissions_room, idx_participants_room.
 - Enable RLS on all host-owned tables with host_id = auth.uid() policies (defense-in-depth;
 backend bypasses via service role). No client-readable path to correct_option_ids.

 0003_functions.sql — SECURITY DEFINER plpgsql functions:
 - start_question(p_participant, p_question) returns timestamptz — INSERT ... ON CONFLICT DO NOTHING into question_starts,
 then return the canonical started_at.
 - record_answer(p_room, p_participant, p_question, p_selected, p_started, p_submitted, p_latency_ms, p_is_correct, p_points)
 returns boolean — INSERT INTO submissions ... ON CONFLICT (participant_id, question_id) DO NOTHING; only if a row was
 actually inserted,
 UPDATE participants SET score_total += p_points, time_total_ms += p_latency_ms, current_question_index += 1. Returns whether
 it newly scored. This is exactly-once, one txn.
 - sweep_room(p_room) — for every (participant, question) in the room with no submission,
 insert a 0-point/null-option/latency = duration_ms row and fold into aggregates; mark
 participants finished. Used by /end (and reused by the Phase 3 finalizer).

 Backend (backend/app/)

 - config.py — add supabase_service_key: str = "".
 - supabase_client.py — add get_service_supabase() (service-role client, used for all
 writes/reads of play data). Keep the existing anon client for auth.get_user() JWT checks.
 - scoring.py — pure score(correct, latency_ms, duration_ms, base) -> int exactly as
 SystemDesign.md §4 (round(base * (1 - 0.5 * min(latency,duration)/duration)), 0 if wrong).
 Pure and unit-tested in isolation.
 - auth.py — FastAPI dependency current_host → validates Authorization: Bearer <jwt>
 via anon_client.auth.get_user(token), returns the user id; 401 on failure.
 - schemas.py — pydantic request/response models (create room, bulk questions, join, answer,
 question payload without correct answers, results row).
 - rooms.py (host router, all behind current_host + host_id ownership check):
 POST /rooms, PUT /rooms/{id}, POST /rooms/{id}/questions (bulk replace),
 POST /rooms/{id}/ready (generate collision-safe 6-digit code w/ retry on unique conflict →
 ready), POST /rooms/{id}/schedule {start_at}, POST /rooms/{id}/start (T0=now, sets
 end_at = T0 + Σduration + grace, live), POST /rooms/{id}/end (closed + sweep_room),
 GET /rooms/{id}/results.
 - play.py (participant router, anonymous, service-role):
   - POST /join {code, display_name} → create participant, return {session_token, room_state}.
   - POST /rooms/{code}/next → assert live and now < end_at; loop: start_question for the
 question at current_question_index; if now > started_at + duration + grace, record_answer
 a 0 (advances index) and continue; else return the question payload (no correct answers) +
 absolute deadline + server_now. Idempotent: re-requesting returns the same deadline.
 When index ≥ question count → mark finished, return done.
   - POST /rooms/{code}/answer {question_id, option_id} → assert current question + started;
 compute is_correct (option ∈ correct_option_ids) and latency = now - started_at; if past
 deadline + grace → 0/expired; call record_answer; ack (no correctness during live window).
   - GET /rooms/{code}/me?token= → participant state / current rank.
 - main.py — include rooms and play routers; keep /health.
 - Session tokens: authenticate participant writes by requiring the session_token (returned at
 join) on next/answer/me.

 Frontend (frontend/src/)

 Thin but real; keep minimal — state-based view switching, no react-router dependency.
 - Add @supabase/supabase-js; lib/supabase.js client from VITE_SUPABASE_URL /
 VITE_SUPABASE_ANON_KEY; lib/api.js fetch helper (Bearer for host calls).
 - Host view: Google sign-in (signInWithOAuth({provider:'google'})) → RoomBuilder (title +
 questions: prompt, options, mark correct, duration, points) → Ready (show join code) → Start →
 Results (poll GET /results).
 - Participant view: Join (code + name) → Play loop (/next → render question + countdown from
 absolute deadline with server_now offset → submit → auto-advance; auto-submit at deadline) →
 Finished → Results (poll). Full rehydrate on refresh via /me + re-/next.
 - Replace the Phase-0 hello App.jsx with a simple role switcher (Host / Join).

 Env / config (user actions)

 - Backend .env + .env.example: add SUPABASE_SERVICE_ROLE_KEY.
 - Frontend .env + .env.example: add VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY.
 - User: in Supabase → Auth → Providers, enable Google and paste Client ID/Secret from
 Google Cloud Console (user owns the console side); add the app's redirect URL. Copy the
 service-role key into backend env. For deploy, set these as sync:false vars in Render later.

 Tests

 - backend/tests/test_scoring.py — pytest for score(): instant→base, buzzer→base/2,
 wrong→0, clamps latency > duration. Add pytest as a dev dependency (uv add --dev pytest).
 - Idempotency/expiry are DB-level (ON CONFLICT, record_answer guard) — verified via the manual
 end-to-end run below (full integration harness is out of Phase 1 scope).

 Verification (Phase 1 "Done when")

 Run docker compose up (backend) + npm run dev (frontend), one host + 2–3 participant tabs:
 1. uv run pytest → scoring unit tests pass.
 2. Host builds a room, marks ready (6-digit code), starts → participants join and play a full
 contest end-to-end via the API.
 3. Double-submit the same answer → scored once (aggregates unchanged on the 2nd call).
 4. Let a question expire without answering → next question records the lapsed one as 0.
 5. Refresh mid-question → resumes the exact remaining time (absolute deadline holds).
 6. GET /results ranks by score_total DESC, time_total_ms ASC, including ties.
 7. POST /end → sweep_room zero-fills every unanswered question; results are complete.


 Phase 1 — What was built

  New files

  ┌──────────────────────────────────────────┬─────────────────────────────────────────────────────────────────────────────┐
  │                   File                   │                                What it does                                 │
  ├──────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────┤
  │ supabase/migrations/0002_core_schema.sql │ All core tables: rooms, questions, participants, submissions, room_results, │
  │                                          │  question_starts; enums; indexes; RLS policies                              │
  ├──────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────┤
  │                                          │ 3 SECURITY DEFINER functions: start_question (idempotent timer),            │
  │ supabase/migrations/0003_functions.sql   │ record_answer (exactly-once scoring in one transaction), sweep_room         │
  │                                          │ (zero-fill unanswered at room end)                                          │
  ├──────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────┤
  │ backend/app/scoring.py                   │ Pure score() function — speed-weighted, Kahoot-style                        │
  ├──────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────┤
  │ backend/app/auth.py                      │ current_host FastAPI dependency — validates Google OAuth JWT via            │
  │                                          │ supabase.auth.get_user()                                                    │
  ├──────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────┤
  │ backend/app/schemas.py                   │ Pydantic models — correct_option_ids is accepted from host but never in any │
  │                                          │  response                                                                   │
  ├──────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────┤
  │ backend/app/rooms.py                     │ Full host API: create, edit, bulk questions, ready (6-digit code),          │
  │                                          │ schedule, start, end, results                                               │
  ├──────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────┤
  │ backend/app/play.py                      │ Participant API: join, next (idempotent + expiry loop), answer, me          │
  ├──────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────┤
  │ backend/tests/test_scoring.py            │ 7 unit tests — all passing                                                  │
  ├──────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────┤
  │ frontend/src/lib/supabase.js             │ Supabase client (for host Google OAuth)                                     │
  ├──────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────┤
  │ frontend/src/lib/api.js                  │ Fetch helper (Bearer token + JSON body)                                     │
  └──────────────────────────────────────────┴─────────────────────────────────────────────────────────────────────────────┘

  Modified files

  - backend/app/config.py — added supabase_service_key
  - backend/app/supabase_client.py — added get_service_supabase() (service-role client)
  - backend/app/main.py — wired in both routers
  - frontend/src/App.jsx — full UI: host flow (sign-in → builder → ready → live → results) + participant flow (join → play
  loop → done)
  - frontend/src/App.css — replaced Vite template CSS with app styles
  - render.yaml — added SUPABASE_SERVICE_KEY, VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY
