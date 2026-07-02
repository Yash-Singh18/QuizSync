import { useEffect, useState } from 'react'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

function App() {
  const [health, setHealth] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch(`${API_URL}/health`)
      .then((res) => res.json())
      .then(setHealth)
      .catch((err) => setError(err.message))
  }, [])

  return (
    <main style={{ fontFamily: 'sans-serif', padding: '2rem' }}>
      <h1>QuizSync</h1>
      {error && <p>Backend unreachable: {error}</p>}
      {health && (
        <p>
          API status: {health.status} — Supabase connected: {String(health.supabase)}
        </p>
      )}
      {!health && !error && <p>Loading…</p>}
    </main>
  )
}

export default App
