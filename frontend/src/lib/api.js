const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

/**
 * Thin fetch wrapper.
 * @param {string} path - relative path, e.g. '/rooms'
 * @param {object} [opts] - fetch options; add `token` for Bearer auth, `json` for body
 */
export async function api(path, { token, json, method = 'GET', params } = {}) {
  const url = new URL(API_URL + path)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      url.searchParams.set(k, v)
    }
  }

  const headers = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (json !== undefined) headers['Content-Type'] = 'application/json'

  const res = await fetch(url.toString(), {
    method: json !== undefined ? (method === 'GET' ? 'POST' : method) : method,
    headers,
    body: json !== undefined ? JSON.stringify(json) : undefined,
  })

  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? JSON.stringify(body)
    } catch (_) {}
    throw new Error(detail)
  }

  if (res.status === 204) return null
  return res.json()
}
