import { BrowserRouter, Routes, Route, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Sidebar } from './components/layout/Sidebar'
import { TopBar } from './components/layout/TopBar'
import { Overview } from './pages/Overview'
import { Sessions } from './pages/Sessions'
import { Activity } from './pages/Activity'
import { DeepDive } from './pages/DeepDive'
import { useWebSocket } from './hooks/useWebSocket'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 2,
      staleTime: 30_000,
    },
  },
})

const PAGE_TITLES: Record<string, string> = {
  '/': 'Overview',
  '/sessions': 'Sessions',
  '/activity': 'Activity',
  '/deep-dive': 'Deep Dive',
}

function Layout() {
  const location = useLocation()
  const title = PAGE_TITLES[location.pathname] ?? 'AAItrade'
  useWebSocket()

  return (
    <div className="flex h-screen overflow-hidden bg-gray-950">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <TopBar title={title} />
        <main className="flex-1 overflow-y-auto">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/activity" element={<Activity />} />
            <Route path="/deep-dive" element={<DeepDive />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Layout />
      </BrowserRouter>
    </QueryClientProvider>
  )
}
