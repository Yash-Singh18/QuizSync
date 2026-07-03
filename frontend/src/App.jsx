import { useEffect, useRef, useState } from 'react'
import { supabase } from './lib/supabase'
import { api } from './lib/api'
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
      if (data.session) setSession(data.session)
    })
    const { data: sub } = supabase.auth.onAuthStateChange((_e, s) => {
      setSession(s)
      if (s) setStage('build')
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

  async function handleStart() {
    setStarting(true)
    onError(null)
    try {
      const liveRoom = await api(`/rooms/${room.id}/start`, { token, json: {}, method: 'POST' })
      onStart(liveRoom)
    } catch (err) {
      onError(err.message)
      setStarting(false)
    }
  }

  return (
    <main className="center">
      <h2>{room.title}</h2>
      <p>Share this join code with participants:</p>
      <div className="join-code">{room.join_code}</div>
      {error && <p className="error">{error}</p>}
      <button className="btn primary" onClick={handleStart} disabled={starting}>
        {starting ? 'Starting…' : 'Start Quiz Now'}
      </button>
    </main>
  )
}

// ── LiveMonitor ────────────────────────────────────────────────────────────

function LiveMonitor({ room, token, onEnd, onError, error }) {
  const [results, setResults] = useState([])
  const [ending, setEnding] = useState(false)
  const intervalRef = useRef(null)

  useEffect(() => {
    fetchResults()
    intervalRef.current = setInterval(fetchResults, 3000)
    return () => clearInterval(intervalRef.current)
  }, [])

  async function fetchResults() {
    try {
      const data = await api(`/rooms/${room.id}/results`, { token })
      setResults(data)
    } catch (_) {}
  }

  async function handleEnd() {
    setEnding(true)
    onError(null)
    try {
      const closedRoom = await api(`/rooms/${room.id}/end`, { token, json: {}, method: 'POST' })
      clearInterval(intervalRef.current)
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
        <span className="badge live">LIVE</span>
      </header>
      {error && <p className="error">{error}</p>}
      <Leaderboard rows={results} />
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
  const [stage, setStage] = useState('join') // join | play | done
  const [session, setSession] = useState(null)  // { session_token, room_id, code }
  const [error, setError] = useState(null)

  if (stage === 'join') {
    return (
      <JoinScreen
        onJoined={(s) => { setSession(s); setStage('play') }}
        onError={setError}
        error={error}
        onBack={onExit}
      />
    )
  }

  if (stage === 'play') {
    return (
      <PlayScreen
        session={session}
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

function PlayScreen({ session, onDone, onError, error }) {
  const [question, setQuestion] = useState(null)
  const [selected, setSelected] = useState(null)
  const [submitted, setSubmitted] = useState(false)
  const [remaining, setRemaining] = useState(null)
  const [loading, setLoading] = useState(true)
  const timerRef = useRef(null)

  useEffect(() => {
    fetchNext()
    return () => clearInterval(timerRef.current)
  }, [])

  async function fetchNext() {
    setLoading(true)
    setSelected(null)
    setSubmitted(false)
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

      // Compute clock offset: server_now vs local now
      const serverNow = new Date(data.server_now).getTime()
      const localNow = Date.now()
      const offset = serverNow - localNow   // positive means server is ahead

      const deadline = new Date(data.deadline).getTime()
      setQuestion(data)

      // Countdown timer using absolute deadline
      function tick() {
        const msLeft = deadline - (Date.now() + offset)
        if (msLeft <= 0) {
          setRemaining(0)
          clearInterval(timerRef.current)
          // Auto-advance after deadline
          setTimeout(fetchNext, 800)
        } else {
          setRemaining(Math.ceil(msLeft / 1000))
        }
      }
      tick()
      timerRef.current = setInterval(tick, 250)
    } catch (err) {
      onError(err.message)
    } finally {
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

  const timerColor = remaining <= 5 ? '#e53e3e' : remaining <= 10 ? '#dd6b20' : '#38a169'

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
    </main>
  )
}

// ── DoneScreen ────────────────────────────────────────────────────────────

function DoneScreen({ session, onExit }) {
  const [me, setMe] = useState(null)

  useEffect(() => {
    api(`/rooms/${session.code}/me`, {
      method: 'GET',
      params: { token: session.session_token },
    })
      .then(setMe)
      .catch(() => {})
  }, [])

  return (
    <main className="center">
      <h2>🎉 You're done!</h2>
      {me && (
        <div className="done-stats">
          <p>Score: <strong>{me.score_total}</strong></p>
          <p>Time: <strong>{(me.time_total_ms / 1000).toFixed(1)}s</strong></p>
          {me.rank && <p>Rank: <strong>#{me.rank}</strong></p>}
        </div>
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
            <td>{r.status}</td>
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
