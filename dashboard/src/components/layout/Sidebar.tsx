import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  BarChart3,
  Activity,
  BookOpen,
  TrendingUp,
} from 'lucide-react'

const navItems = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/sessions', label: 'Sessions', icon: BarChart3, end: false },
  { to: '/activity', label: 'Activity', icon: Activity, end: false },
  { to: '/deep-dive', label: 'Deep Dive', icon: BookOpen, end: false },
]

export function Sidebar() {
  return (
    <aside className="w-56 flex-shrink-0 bg-gray-950 border-r border-gray-800 flex flex-col h-screen sticky top-0">
      {/* Logo */}
      <div className="px-5 py-5 border-b border-gray-800">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-emerald-500/20 flex items-center justify-center">
            <TrendingUp size={16} className="text-emerald-400" />
          </div>
          <div>
            <div className="text-sm font-bold text-gray-100 leading-none">AAItrade</div>
            <div className="text-xs text-gray-500 mt-0.5">Dashboard</div>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {navItems.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                isActive
                  ? 'bg-gray-800 text-gray-100'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/60'
              }`
            }
          >
            <Icon size={16} />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-5 py-4 border-t border-gray-800">
        <div className="text-xs text-gray-600">Autonomous AI Trading</div>
        <div className="text-xs text-gray-700 mt-0.5">Phase 1 — NSE/BSE</div>
      </div>
    </aside>
  )
}
