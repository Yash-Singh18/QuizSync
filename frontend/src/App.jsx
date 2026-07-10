import { useEffect, useRef, useState } from 'react'
import { supabase } from './lib/supabase'
import { api } from './lib/api'
import { useRoomSocket } from './lib/useRoomSocket'
import './App.css'

// ── top-level role switcher ───────────────────────────────────────────────

export default function App() {
  const [role, setRole] = useState(null) // 'host' | 'participant'

  if (role === 'host') return <HostApp onExit={() => setRole(null)} />
  if (role === 'participant') return <ParticipantApp onExit={() => setRole(null)} />

  return (
    <main className="center">
      <h1>QuizSync</h1>
      <p>Real-time quiz contests</p>
      <div className="role-buttons">
        <button className="btn primary" onClick={() => setRole('host')}>
          Host a Quiz
        </button>
        <button className="btn secondary" onClick={() => setRole('participant')}>
          Join a Quiz
        </button>
      </div>
    </main>
  )
}

// ══════════════════════════════════════════════════════════════════════════
// HOST FLOW
// sign in → build room → ready (join code) → start → results
// ══════════════════════════════════════════════════════════════════════════

function HostApp({ onExit }) {
  const [session, setSession] = useState(null)
  const [stage, setStage] = useState('auth') // auth | build | ready | live | results
  const [room, setRoom] = useState(null)
  const [error, setError] = useState(null)

  // Listen for OAuth redirect
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (data.session) {
        setSession(data.session)
        setStage(prev => prev === 'auth' ? 'build' : prev)
      }
    })
    const { data: sub } = supabase.auth.onAuthStateChange((event, s) => {
      setSession(s)
      // INITIAL_SESSION (page reload after redirect) and SIGNED_IN both mean "logged in"
      if (s) setStage(prev => prev === 'auth' ? 'build' : prev)
      if (event === 'SIGNED_OUT') setStage('auth')
    })
    return () => sub.subscription.unsubscribe()
  }, [])

  async function signIn() {
    setError(null)
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: window.location.origin },
    })
    if (error) setError(error.message)
  }

  async function signOut() {
    await supabase.auth.signOut()
    setSession(null)
    setRoom(null)
    setStage('auth')
  }

  if (stage === 'auth' || !session) {
    return (
      <main className="center">
        <BackButton onClick={onExit} />
        <h2>Host Sign-in</h2>
        {error && <p className="error">{error}</p>}
        <button className="btn primary" onClick={signIn}>
          Sign in with Google
        </button>
      </main>
    )
  }

  if (stage === 'build') {
    return (
      <RoomBuilder
        token={session.access_token}
        onRoomReady={(r) => { setRoom(r); setStage('ready') }}
        onSignOut={signOut}
        onError={setError}
        error={error}
      />
    )
  }

  if (stage === 'ready') {
    return (
      <ReadyScreen
        room={room}
        token={session.access_token}
        onStart={(r) => { setRoom(r); setStage('live') }}
        onError={setError}
        error={error}
      />
    )
  }

  if (stage === 'live') {
    return (
      <LiveMonitor
        room={room}
        token={session.access_token}
        onEnd={(r) => { setRoom(r); setStage('results') }}
        onError={setError}
        error={error}
      />
    )
  }

  if (stage === 'results') {
    return (
      <ResultsScreen
        room={room}
        token={session.access_token}
        onDone={signOut}
      />
    )
  }
}

// ── RoomBuilder ────────────────────────────────────────────────────────────

function RoomBuilder({ token, onRoomReady, onSignOut, onError, error }) {
  const [title, setTitle] = useState('')
  const [questions, setQuestions] = useState([newQuestion()])
  const [saving, setSaving] = useState(false)

  function newQuestion() {
    return {
      prompt: '',
      options: [{ id: 'a', text: '' }, { id: 'b', text: '' }, { id: 'c', text: '' }, { id: 'd', text: '' }],
      correct_option_ids: [],
      duration_ms: 30000,
      base_points: 1000,
    }
  }

  function addQuestion() {
    setQuestions(qs => [...qs, newQuestion()])
  }

  function removeQuestion(i) {
    setQuestions(qs => qs.filter((_, idx) => idx !== i))
  }

  function updateQuestion(i, patch) {
    setQuestions(qs => qs.map((q, idx) => idx === i ? { ...q, ...patch } : q))
  }

  function updateOption(qi, oi, text) {
    setQuestions(qs => qs.map((q, idx) => {
      if (idx !== qi) return q
      const opts = q.options.map((o, oidx) => oidx === oi ? { ...o, text } : o)
      return { ...q, options: opts }
    }))
  }

  function toggleCorrect(qi, optId) {
    setQuestions(qs => qs.map((q, idx) => {
      if (idx !== qi) return q
      const ids = q.correct_option_ids.includes(optId)
        ? q.correct_option_ids.filter(id => id !== optId)
        : [...q.correct_option_ids, optId]
      return { ...q, correct_option_ids: ids }
    }))
  }

  async function handleSubmit(e) {
    e.preventDefault()
    onError(null)

    // Validate
    for (let i = 0; i < questions.length; i++) {
      const q = questions[i]
      if (!q.prompt.trim()) { onError(`Question ${i + 1}: prompt is required`); return }
      if (q.options.some(o => !o.text.trim())) { onError(`Question ${i + 1}: fill in all options`); return }
      if (q.correct_option_ids.length === 0) { onError(`Question ${i + 1}: mark at least one correct option`); return }
    }

    setSaving(true)
    try {
      const room = await api('/rooms', { token, json: { title }, method: 'POST' })
      await api(`/rooms/${room.id}/questions`, {
        token,
        json: questions,
        method: 'POST',
      })
      const readyRoom = await api(`/rooms/${room.id}/ready`, { token, json: {}, method: 'POST' })
      onRoomReady(readyRoom)
    } catch (err) {
      onError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <main className="builder">
      <header className="builder-header">
        <h2>Build Your Quiz</h2>
        <button className="btn ghost small" onClick={onSignOut}>Sign out</button>
      </header>
      {error && <p className="error">{error}</p>}
      <form onSubmit={handleSubmit}>
        <label className="field">
          <span>Quiz title</span>
          <input
            required
            value={title}
            onChange={e => setTitle(e.target.value)}
            placeholder="e.g. JavaScript Trivia"
          />
        </label>

        {questions.map((q, qi) => (
          <div key={qi} className="question-card">
            <div className="question-card-header">
              <strong>Q{qi + 1}</strong>
              {questions.length > 1 && (
                <button type="button" className="btn ghost small" onClick={() => removeQuestion(qi)}>
                  Remove
                </button>
              )}
            </div>

            <label className="field">
              <span>Prompt</span>
              <input
                required
                value={q.prompt}
                onChange={e => updateQuestion(qi, { prompt: e.target.value })}
                placeholder="What is...?"
              />
            </label>

            <div className="options-grid">
              {q.options.map((opt, oi) => (
                <label key={opt.id} className={`option-label ${q.correct_option_ids.includes(opt.id) ? 'correct' : ''}`}>
                  <input
                    type="checkbox"
                    checked={q.correct_option_ids.includes(opt.id)}
                    onChange={() => toggleCorrect(qi, opt.id)}
                  />
                  <input
                    required
                    value={opt.text}
                    onChange={e => updateOption(qi, oi, e.target.value)}
                    placeholder={`Option ${opt.id.toUpperCase()}`}
                  />
                </label>
              ))}
            </div>

            <div className="question-meta">
              <label className="field inline">
                <span>Time (s)</span>
                <input
                  type="number"
                  min="5"
                  max="120"
                  value={q.duration_ms / 1000}
                  onChange={e => updateQuestion(qi, { duration_ms: Number(e.target.value) * 1000 })}
                />
              </label>
              <label className="field inline">
                <span>Points</span>
                <input
                  type="number"
                  min="100"
                  step="100"
                  value={q.base_points}
                  onChange={e => updateQuestion(qi, { base_points: Number(e.target.value) })}
                />
              </label>
            </div>
          </div>
        ))}

        <button type="button" className="btn secondary" onClick={addQuestion}>
          + Add Question
        </button>

        <button type="submit" className="btn primary" disabled={saving}>
          {saving ? 'Saving…' : 'Save & Get Join Code'}
        </button>
      </form>
    </main>
  )
}

// ── ReadyScreen ────────────────────────────────────────────────────────────

function ReadyScreen({ room, token, onStart, onError, error }) {
  const [starting, setStarting] = useState(false)
  const [participants, setParticipants] = useState([])
  const [startAt, setStartAt] = useState('')       // datetime-local value
  const [scheduledRoom, setScheduledRoom] = useState(
    room.status === 'scheduled' ? room : null
  )
  const [scheduling, setScheduling] = useState(false)
  const intervalRef = useRef(null)

  useEffect(() => {
    function poll() {
      api(`/rooms/${room.id}/results`, { token })
        .then(setParticipants)
        .catch(() => {})
    }
    poll()
    intervalRef.current = setInterval(poll, 2000)
    return () => clearInterval(intervalRef.current)
  }, [])

  // Once scheduled, watch for the worker flipping the room live at T0
  useEffect(() => {
    if (!scheduledRoom) return
    const id = setInterval(() => {
      api(`/rooms/${room.id}`, { token })
        .then((r) => {
          if (r.status === 'live') {
            clearInterval(id)
            clearInterval(intervalRef.current)
            onStart(r)
          }
        })
        .catch(() => {})
    }, 2000)
    return () => clearInterval(id)
  }, [scheduledRoom])

  async function handleStart() {
    setStarting(true)
    onError(null)
    clearInterval(intervalRef.current)
    try {
      const liveRoom = await api(`/rooms/${room.id}/start`, { token, json: {}, method: 'POST' })
      onStart(liveRoom)
    } catch (err) {
      onError(err.message)
      setStarting(false)
    }
  }

  async function handleSchedule() {
    setScheduling(true)
    onError(null)
    try {
      const r = await api(`/rooms/${room.id}/schedule`, {
        token,
        json: { start_at: new Date(startAt).toISOString() },
        method: 'POST',
      })
      setScheduledRoom(r)
    } catch (err) {
      onError(err.message)
    } finally {
      setScheduling(false)
    }
  }

  return (
    <main className="builder">
      <h2>{room.title}</h2>
      <p>Share this join code with participants:</p>
      <div className="join-code">{room.join_code}</div>

      <div className="participant-list">
        <strong>{participants.length} joined</strong>
        {participants.length > 0 && (
          <ul>
            {participants.map(p => (
              <li key={p.participant_id}>{p.display_name}</li>
            ))}
          </ul>
        )}
        {participants.length === 0 && <p className="muted">Waiting for participants…</p>}
      </div>

      {error && <p className="error">{error}</p>}

      {scheduledRoom ? (
        <p className="scheduled-note">
          Scheduled for <strong>{new Date(scheduledRoom.start_at).toLocaleString()}</strong>
          {' '}— participants will be released automatically.
        </p>
      ) : (
        <div className="schedule-row">
          <input
            type="datetime-local"
            value={startAt}
            onChange={e => setStartAt(e.target.value)}
          />
          <button
            className="btn secondary"
            onClick={handleSchedule}
            disabled={!startAt || scheduling}
          >
            {scheduling ? 'Scheduling…' : 'Schedule'}
          </button>
        </div>
      )}

      <button
        className="btn primary"
        onClick={handleStart}
        disabled={starting || participants.length === 0}
      >
        {starting ? 'Starting…' : `Start Quiz Now (${participants.length} players)`}
      </button>
    </main>
  )
}

// ── LiveMonitor ────────────────────────────────────────────────────────────

function LiveMonitor({ room, token, onEnd, onError, error }) {
  const [initialResults, setInitialResults] = useState([])
  const [ending, setEnding] = useState(false)
  const { board, connected, finalized } = useRoomSocket({ code: room.join_code, role: 'host', token })

  // One-time fill until the first WS snapshot arrives
  useEffect(() => {
    api(`/rooms/${room.id}/results`, { token })
      .then(setInitialResults)
      .catch(() => {})
  }, [])

  // Scheduler hit T_end and finalized the room
  useEffect(() => {
    if (finalized) onEnd({ ...room, status: 'finalized' })
  }, [finalized])

  const rows = board.length ? board : initialResults

  async function handleEnd() {
    setEnding(true)
    onError(null)
    try {
      const closedRoom = await api(`/rooms/${room.id}/end`, { token, json: {}, method: 'POST' })
      onEnd(closedRoom)
    } catch (err) {
      onError(err.message)
      setEnding(false)
    }
  }

  return (
    <main className="builder">
      <header className="builder-header">
        <h2>Live — {room.title}</h2>
        <span className="badge live">{connected ? 'LIVE' : 'RECONNECTING…'}</span>
      </header>
      {error && <p className="error">{error}</p>}
      <Leaderboard rows={rows} />
      <button className="btn danger" onClick={handleEnd} disabled={ending}>
        {ending ? 'Ending…' : 'End Quiz'}
      </button>
    </main>
  )
}

// ── ResultsScreen ──────────────────────────────────────────────────────────

function ResultsScreen({ room, token, onDone }) {
  const [results, setResults] = useState([])

  useEffect(() => {
    api(`/rooms/${room.id}/results`, { token })
      .then(setResults)
      .catch(() => {})
  }, [])

  return (
    <main className="builder">
      <h2>Final Results — {room.title}</h2>
      <Leaderboard rows={results} />
      <button className="btn primary" onClick={onDone}>Done</button>
    </main>
  )
}

// ══════════════════════════════════════════════════════════════════════════
// PARTICIPANT FLOW
// join → play loop (next → countdown → answer → next…) → done → results
// ══════════════════════════════════════════════════════════════════════════

function ParticipantApp({ onExit }) {
  const [stage, setStage] = useState('join') // join | lobby | play | done
  const [session, setSession] = useState(null)  // { session_token, room_id, code }
  const [initialQuestion, setInitialQuestion] = useState(null) // Q1 from the `go` event
  const [error, setError] = useState(null)

  if (stage === 'join') {
    return (
      <JoinScreen
        onJoined={(s) => { setSession(s); setStage('lobby') }}
        onError={setError}
        error={error}
        onBack={onExit}
      />
    )
  }

  if (stage === 'lobby') {
    return (
      <LobbyScreen
        session={session}
        onStart={(q) => { setInitialQuestion(q ?? null); setStage('play') }}
        onError={setError}
        error={error}
      />
    )
  }

  if (stage === 'play') {
    return (
      <PlayScreen
        session={session}
        initialQuestion={initialQuestion}
        onDone={() => setStage('done')}
        onError={setError}
        error={error}
      />
    )
  }

  if (stage === 'done') {
    return (
      <DoneScreen
        session={session}
        onExit={onExit}
      />
    )
  }
}

// ── LobbyScreen ───────────────────────────────────────────────────────────

function LobbyScreen({ session, onStart, onError }) {
  const intervalRef = useRef(null)
  const startedRef = useRef(false)
  const { roomState, go, connected } = useRoomSocket({
    code: session.code,
    role: 'participant',
    token: session.session_token,
  })

  function release(question) {
    if (startedRef.current) return
    startedRef.current = true
    clearInterval(intervalRef.current)
    onStart(question)
  }

  // Primary release path: the `go` event at T0 carries Q1 — no /next stampede
  useEffect(() => {
    if (go) release(go.question)
  }, [go])

  // Slow fallback poll in case the socket missed the go event
  useEffect(() => {
    function poll() {
      api(`/rooms/${session.code}/me`, {
        method: 'GET',
        params: { token: session.session_token },
      })
        .then((data) => {
          if (data.room_status === 'live') release(null)
        })
        .catch(() => {})
    }
    intervalRef.current = setInterval(poll, 5000)
    return () => clearInterval(intervalRef.current)
  }, [])

  const seconds = roomState?.seconds_to_start

  return (
    <main className="center">
      <h2>You're in! 🎉</h2>
      {seconds != null && seconds > 0 ? (
        <>
          <p className="muted">Quiz starts in</p>
          <div className="lobby-countdown">{formatCountdown(seconds)}</div>
        </>
      ) : (
        <p className="muted">Waiting for the quiz to start…</p>
      )}
      <div className="spinner" />
      {!connected && <p className="muted">Reconnecting…</p>}
    </main>
  )
}

function formatCountdown(totalSeconds) {
  const m = Math.floor(totalSeconds / 60)
  const s = totalSeconds % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

// ── JoinScreen ────────────────────────────────────────────────────────────

function JoinScreen({ onJoined, onError, error, onBack }) {
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [joining, setJoining] = useState(false)

  async function handleJoin(e) {
    e.preventDefault()
    setJoining(true)
    onError(null)
    try {
      const data = await api('/join', { json: { code, display_name: name }, method: 'POST' })
      onJoined({ ...data, code })
    } catch (err) {
      onError(err.message)
    } finally {
      setJoining(false)
    }
  }

  return (
    <main className="center">
      <BackButton onClick={onBack} />
      <h2>Join a Quiz</h2>
      {error && <p className="error">{error}</p>}
      <form onSubmit={handleJoin} className="join-form">
        <input
          required
          placeholder="6-digit code"
          maxLength={6}
          value={code}
          onChange={e => setCode(e.target.value.replace(/\D/g, ''))}
        />
        <input
          required
          placeholder="Your display name"
          maxLength={64}
          value={name}
          onChange={e => setName(e.target.value)}
        />
        <button type="submit" className="btn primary" disabled={joining}>
          {joining ? 'Joining…' : 'Join'}
        </button>
      </form>
    </main>
  )
}

// ── PlayScreen ────────────────────────────────────────────────────────────

function PlayScreen({ session, initialQuestion, onDone, onError, error }) {
  const [question, setQuestion] = useState(null)
  const [selected, setSelected] = useState(null)
  const [submitted, setSubmitted] = useState(false)
  const [remaining, setRemaining] = useState(null)
  const [loading, setLoading] = useState(true)
  const timerRef = useRef(null)
  const fetchingRef = useRef(false)
  const { you, total, finalized } = useRoomSocket({
    code: session.code,
    role: 'participant',
    token: session.session_token,
  })

  useEffect(() => {
    // Q1 arrives in the `go` event — skip the /next stampede at T0
    if (initialQuestion) beginQuestion(initialQuestion)
    else fetchNext()
    return () => clearInterval(timerRef.current)
  }, [])

  // Scheduler closed + finalized the room → final screen
  useEffect(() => {
    if (finalized) onDone()
  }, [finalized])

  function beginQuestion(data) {
    // Compute clock offset: server_now vs local now
    const serverNow = new Date(data.server_now).getTime()
    const localNow = Date.now()
    const offset = serverNow - localNow   // positive means server is ahead

    const deadline = new Date(data.deadline).getTime()
    setQuestion(data)
    setSelected(null)
    setSubmitted(false)
    setLoading(false)

    // Countdown timer using absolute deadline. Clear by captured id, not
    // timerRef — timerRef may already point at a newer interval.
    const intervalId = setInterval(tick, 250)
    timerRef.current = intervalId
    function tick() {
      const msLeft = deadline - (Date.now() + offset)
      if (msLeft <= 0) {
        setRemaining(0)
        clearInterval(intervalId)
        // Auto-advance after deadline
        setTimeout(fetchNext, 800)
      } else {
        setRemaining(Math.ceil(msLeft / 1000))
      }
    }
    tick()
  }

  async function fetchNext() {
    // Guard against concurrent chains (mount + answer + expiry can race);
    // a second chain would orphan the first interval → runaway /next loop.
    if (fetchingRef.current) return
    fetchingRef.current = true
    setLoading(true)
    onError(null)
    clearInterval(timerRef.current)

    try {
      const data = await api(`/rooms/${session.code}/next`, {
        json: { session_token: session.session_token },
        method: 'POST',
      })

      if (data.done) {
        onDone()
        return
      }

      beginQuestion(data)
    } catch (err) {
      // Contest over (closed / past end) → final screen, don't retry
      if (/room (is not live|has ended)|already finished/i.test(err.message)) {
        onDone()
        return
      }
      onError(err.message)
    } finally {
      fetchingRef.current = false
      setLoading(false)
    }
  }

  async function handleAnswer(optId) {
    if (submitted || remaining === 0) return
    setSelected(optId)
    setSubmitted(true)
    clearInterval(timerRef.current)

    try {
      await api(`/rooms/${session.code}/answer`, {
        json: {
          session_token: session.session_token,
          question_id: question.question_id,
          option_id: optId,
        },
        method: 'POST',
      })
    } catch (err) {
      onError(err.message)
    }

    // Auto-advance to next question after a short pause
    setTimeout(fetchNext, 1200)
  }

  if (loading) return <main className="center"><p>Loading…</p></main>

  if (!question) return <main className="center"><p>Waiting for question…</p></main>

  const timerColor = remaining <= 5 ? 'var(--error)' : remaining <= 10 ? 'var(--bronze)' : 'var(--success)'

  return (
    <main className="play">
      {error && <p className="error">{error}</p>}
      <div className="timer" style={{ color: timerColor }}>
        {remaining !== null ? `${remaining}s` : '…'}
      </div>
      <h2 className="question-prompt">{question.prompt}</h2>
      <div className="options-play">
        {question.options.map(opt => (
          <button
            key={opt.id}
            className={`option-btn ${selected === opt.id ? 'selected' : ''} ${submitted ? 'locked' : ''}`}
            onClick={() => handleAnswer(opt.id)}
            disabled={submitted || remaining === 0}
          >
            {opt.text}
          </button>
        ))}
      </div>
      {submitted && <p className="status-msg">Answer locked — loading next…</p>}
      {you && (
        <p className="rank-line muted">
          You're #{you.rank} of {total} — {you.score_total} pts
        </p>
      )}
    </main>
  )
}

// ── DoneScreen ────────────────────────────────────────────────────────────

function DoneScreen({ session, onExit }) {
  const [me, setMe] = useState(null)
  const [review, setReview] = useState(null)

  useEffect(() => {
    api(`/rooms/${session.code}/me`, {
      method: 'GET',
      params: { token: session.session_token },
    })
      .then(setMe)
      .catch(() => {})
  }, [])

  // Final board + correct answers unlock once the room is finalized;
  // /review returns 409 until then, so retry on a slow poll.
  useEffect(() => {
    let timer
    let cancelled = false
    function fetchReview() {
      api(`/rooms/${session.code}/review`, {
        method: 'GET',
        params: { token: session.session_token },
      })
        .then((data) => { if (!cancelled) setReview(data) })
        .catch(() => { if (!cancelled) timer = setTimeout(fetchReview, 3000) })
    }
    fetchReview()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [])

  return (
    <main className="builder">
      <h2>🎉 You're done!</h2>
      {me && (
        <div className="done-stats">
          <p>Score: <strong>{me.score_total}</strong></p>
          <p>Time: <strong>{(me.time_total_ms / 1000).toFixed(1)}s</strong></p>
          {me.rank && <p>Rank: <strong>#{me.rank}</strong></p>}
        </div>
      )}

      {!review && <p className="muted">Waiting for final results…</p>}
      {review && (
        <>
          <h3>Final Standings</h3>
          <Leaderboard rows={review.results} />
          <h3>Answer Review</h3>
          {review.questions.map(q => (
            <div key={q.question_id} className="review-card">
              <p className="review-prompt">
                <strong>Q{q.order_index + 1}.</strong> {q.prompt}
                <span className={`review-points ${q.is_correct ? 'good' : 'bad'}`}>
                  {q.points_awarded ?? 0} pts
                </span>
              </p>
              <div className="review-options">
                {q.options.map(opt => {
                  const correct = q.correct_option_ids.includes(opt.id)
                  const yours = q.your_option_id === opt.id
                  return (
                    <div
                      key={opt.id}
                      className={`review-option ${correct ? 'correct' : ''} ${yours ? 'yours' : ''}`}
                    >
                      {opt.text}
                      {correct && ' ✓'}
                      {yours && !correct && ' ✗ (your pick)'}
                      {yours && correct && ' (your pick)'}
                    </div>
                  )
                })}
              </div>
              {q.explanation && <p className="muted review-explanation">{q.explanation}</p>}
            </div>
          ))}
        </>
      )}
      <button className="btn primary" onClick={onExit}>Back to Home</button>
    </main>
  )
}

// ══════════════════════════════════════════════════════════════════════════
// Shared components
// ══════════════════════════════════════════════════════════════════════════

function Leaderboard({ rows }) {
  if (!rows.length) return <p className="muted">No participants yet…</p>

  return (
    <table className="leaderboard">
      <thead>
        <tr><th>#</th><th>Name</th><th>Score</th><th>Time (s)</th><th>Status</th></tr>
      </thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.participant_id}>
            <td>{r.rank}</td>
            <td>{r.display_name}</td>
            <td>{r.score_total}</td>
            <td>{(r.time_total_ms / 1000).toFixed(1)}</td>
            <td>{r.status ?? '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function BackButton({ onClick }) {
  return (
    <button className="btn ghost small back-btn" onClick={onClick}>
      ← Back
    </button>
  )
}
