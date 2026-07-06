import { useEffect, useRef, useState } from 'react'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
const PING_INTERVAL_MS = 25000
const MAX_BACKOFF_MS = 15000

/**
 * Live leaderboard socket. Reconnects with exponential backoff.
 * @param {object} opts
 * @param {string} opts.code - room join code
 * @param {'participant'|'host'} opts.role
 * @param {string} opts.token - session_token (participant) or Supabase JWT (host)
 * @param {boolean} [opts.enabled=true]
 * @returns {{ board: array, you: object|null, total: number, connected: boolean }}
 */
export function useRoomSocket({ code, role, token, enabled = true }) {
  const [board, setBoard] = useState([])
  const [you, setYou] = useState(null)
  const [total, setTotal] = useState(0)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef(null)
  const timersRef = useRef({ ping: null, reconnect: null })
  const backoffRef = useRef(1000)

  useEffect(() => {
    if (!enabled || !code || !token) return

    let closed = false

    function connect() {
      const wsBase = API_URL.replace(/^http/, 'ws')
      const ws = new WebSocket(
        `${wsBase}/ws/rooms/${code}?role=${role}&token=${encodeURIComponent(token)}`
      )
      wsRef.current = ws

      ws.onopen = () => {
        backoffRef.current = 1000
        setConnected(true)
        timersRef.current.ping = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' }))
        }, PING_INTERVAL_MS)
      }

      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data)
        if (msg.type === 'lb') {
          setBoard(msg.top)
          setYou(msg.you)
          setTotal(msg.total)
        }
      }

      ws.onclose = () => {
        clearInterval(timersRef.current.ping)
        setConnected(false)
        if (closed) return
        timersRef.current.reconnect = setTimeout(connect, backoffRef.current)
        backoffRef.current = Math.min(backoffRef.current * 2, MAX_BACKOFF_MS)
      }
    }

    connect()

    return () => {
      closed = true
      clearInterval(timersRef.current.ping)
      clearTimeout(timersRef.current.reconnect)
      wsRef.current?.close()
    }
  }, [code, role, token, enabled])

  return { board, you, total, connected }
}
