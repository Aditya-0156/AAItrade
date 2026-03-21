import { useEffect, useRef } from 'react'
import { useAppStore } from '../store'
import type { WsPayload } from '../types'

const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws/feed`
const RECONNECT_DELAY_MS = 3000
const MAX_RECONNECT_DELAY_MS = 30000

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectDelay = useRef(RECONNECT_DELAY_MS)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const { setWsStatus, setLastUpdated, pushFeedItems } = useAppStore()

  useEffect(() => {
    function connect() {
      setWsStatus('connecting')
      const ws = new WebSocket(WS_URL)
      wsRef.current = ws

      ws.onopen = () => {
        setWsStatus('connected')
        reconnectDelay.current = RECONNECT_DELAY_MS
      }

      ws.onmessage = (event) => {
        try {
          const payload: WsPayload = JSON.parse(event.data)
          setLastUpdated(new Date(payload.ts))
          if (payload.events && payload.events.length > 0) {
            pushFeedItems(payload.events)
          }
        } catch {
          // ignore malformed messages
        }
      }

      ws.onerror = () => {
        setWsStatus('error')
      }

      ws.onclose = () => {
        setWsStatus('disconnected')
        wsRef.current = null

        // Exponential backoff reconnect
        reconnectTimer.current = setTimeout(() => {
          reconnectDelay.current = Math.min(
            reconnectDelay.current * 1.5,
            MAX_RECONNECT_DELAY_MS,
          )
          connect()
        }, reconnectDelay.current)
      }
    }

    connect()

    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      if (wsRef.current) {
        wsRef.current.onclose = null
        wsRef.current.close()
      }
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
}
