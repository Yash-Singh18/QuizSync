-- Phase 1: SECURITY DEFINER plpgsql functions for atomic play operations.
-- These run as the DB owner so they can bypass RLS while remaining
-- callable via the anon/service role through .rpc().

-- ── start_question ────────────────────────────────────────────────────────
-- Idempotent timer: first call records started_at; retries return the same
-- value. Direct stand-in for Redis HSETNX.
CREATE OR REPLACE FUNCTION start_question(
  p_participant uuid,
  p_question    uuid
) RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_started timestamptz;
BEGIN
  INSERT INTO question_starts (participant_id, question_id)
  VALUES (p_participant, p_question)
  ON CONFLICT (participant_id, question_id) DO NOTHING;

  SELECT started_at INTO v_started
  FROM question_starts
  WHERE participant_id = p_participant AND question_id = p_question;

  RETURN v_started;
END;
$$;

-- ── record_answer ─────────────────────────────────────────────────────────
-- Exactly-once scoring in a single transaction:
--   1. INSERT submission; skip if duplicate (ON CONFLICT DO NOTHING).
--   2. Only if the row was freshly inserted, UPDATE participant aggregates
--      and advance current_question_index.
-- Returns TRUE if the answer was newly recorded, FALSE if it was a duplicate.
CREATE OR REPLACE FUNCTION record_answer(
  p_room         uuid,
  p_participant  uuid,
  p_question     uuid,
  p_selected     text,          -- null = no answer / expired
  p_started      timestamptz,
  p_submitted    timestamptz,
  p_latency_ms   int,
  p_is_correct   boolean,
  p_points       int
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_inserted boolean;
BEGIN
  INSERT INTO submissions (
    room_id, participant_id, question_id,
    selected_option_id, started_at, submitted_at,
    latency_ms, is_correct, points_awarded
  )
  VALUES (
    p_room, p_participant, p_question,
    p_selected, p_started, p_submitted,
    p_latency_ms, p_is_correct, p_points
  )
  ON CONFLICT (participant_id, question_id) DO NOTHING;

  GET DIAGNOSTICS v_inserted = ROW_COUNT;

  IF v_inserted THEN
    UPDATE participants
    SET
      score_total            = score_total + p_points,
      time_total_ms          = time_total_ms + p_latency_ms,
      current_question_index = current_question_index + 1
    WHERE id = p_participant;
  END IF;

  RETURN v_inserted;
END;
$$;

-- ── sweep_room ────────────────────────────────────────────────────────────
-- Finalizes every unanswered question for every participant in the room:
--   - inserts a 0-point submission (null option, latency = full duration).
--   - advances current_question_index and keeps aggregates correct.
--   - marks every non-finished participant as 'finished'.
-- Safe to call multiple times (ON CONFLICT DO NOTHING on submissions).
CREATE OR REPLACE FUNCTION sweep_room(p_room uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  rec RECORD;
BEGIN
  -- For each (participant, question) pair in the room with no submission yet:
  FOR rec IN
    SELECT
      p.id   AS pid,
      q.id   AS qid,
      q.duration_ms,
      qs.started_at
    FROM participants p
    CROSS JOIN questions q
    LEFT JOIN question_starts qs
      ON qs.participant_id = p.id AND qs.question_id = q.id
    WHERE p.room_id = p_room
      AND q.room_id = p_room
      AND NOT EXISTS (
        SELECT 1 FROM submissions s
        WHERE s.participant_id = p.id AND s.question_id = q.id
      )
  LOOP
    PERFORM record_answer(
      p_room        => p_room,
      p_participant => rec.pid,
      p_question    => rec.qid,
      p_selected    => NULL,
      p_started     => COALESCE(rec.started_at, now()),
      p_submitted   => now(),
      p_latency_ms  => rec.duration_ms,
      p_is_correct  => false,
      p_points      => 0
    );
  END LOOP;

  -- Mark all non-finished participants as finished.
  UPDATE participants
  SET status = 'finished', finished_at = now()
  WHERE room_id = p_room AND status != 'finished';
END;
$$;
