const configuredApiBase = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '')

export function apiUrl(path) {
  return `${configuredApiBase}${path}`
}

export function asrWsUrl() {
  if (import.meta.env.VITE_ASR_WS_URL) return import.meta.env.VITE_ASR_WS_URL
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/api/asr/stream`
}
