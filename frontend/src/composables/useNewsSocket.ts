import { onMounted, onUnmounted } from 'vue'
import type { NewsWsEvent } from '../types/api'

function newsSocketUrl(): string {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${location.host}/api/ws/news`
}

function isNewsUpdated(raw: string): boolean {
  try {
    const payload = JSON.parse(raw) as NewsWsEvent
    return payload.type === 'news_updated'
  } catch {
    return false
  }
}

export function useNewsSocket(onUpdated: () => void): void {
  let socket: WebSocket | null = null
  let closed = false
  let retryMs = 1000
  let reconnectTimer = 0
  let debounceTimer = 0

  function connect() {
    if (closed) {
      return
    }
    socket = new WebSocket(newsSocketUrl())
    socket.addEventListener('open', () => {
      retryMs = 1000
    })
    socket.addEventListener('message', (event) => {
      if (!isNewsUpdated(String(event.data))) {
        return
      }
      window.clearTimeout(debounceTimer)
      debounceTimer = window.setTimeout(onUpdated, 400)
    })
    socket.addEventListener('close', () => {
      socket = null
      if (closed) {
        return
      }
      reconnectTimer = window.setTimeout(() => {
        retryMs = Math.min(retryMs * 2, 15000)
        connect()
      }, retryMs)
    })
  }

  onMounted(connect)
  onUnmounted(() => {
    closed = true
    window.clearTimeout(reconnectTimer)
    window.clearTimeout(debounceTimer)
    socket?.close()
    socket = null
  })
}
