import { create } from 'zustand'
import type { WsEvent } from '../types'

const MAX_FEED_ITEMS = 300

export type WsStatus = 'connecting' | 'connected' | 'disconnected' | 'error'

interface AppState {
  wsStatus: WsStatus
  lastUpdated: Date | null
  feedItems: WsEvent[]
  setWsStatus: (status: WsStatus) => void
  setLastUpdated: (d: Date) => void
  pushFeedItems: (items: WsEvent[]) => void
  clearFeed: () => void
}

export const useAppStore = create<AppState>((set) => ({
  wsStatus: 'disconnected',
  lastUpdated: null,
  feedItems: [],

  setWsStatus: (status) => set({ wsStatus: status }),
  setLastUpdated: (d) => set({ lastUpdated: d }),

  pushFeedItems: (items) =>
    set((state) => {
      const combined = [...items, ...state.feedItems]
      return { feedItems: combined.slice(0, MAX_FEED_ITEMS) }
    }),

  clearFeed: () => set({ feedItems: [] }),
}))
