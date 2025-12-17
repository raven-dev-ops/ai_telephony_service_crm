import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'

import { HealthCard } from './components/HealthCard'
import { SettingsPanel } from './components/SettingsPanel'
import { AdminPage } from './pages/AdminPage'
import { HomePage } from './pages/HomePage'
import { OwnerPage } from './pages/OwnerPage'

import './App.css'

export default function App() {
  return (
    <BrowserRouter>
      <div className="app-shell">
        <header className="app-header">
          <div className="app-brand">Raven CRM</div>
          <nav className="app-nav">
            <NavLink to="/" end>
              Home
            </NavLink>
            <NavLink to="/owner">Owner</NavLink>
            <NavLink to="/admin">Admin</NavLink>
          </nav>
        </header>

        <main className="app-main">
          <div className="app-content">
            <Routes>
              <Route path="/" element={<HomePage />} />
              <Route path="/owner" element={<OwnerPage />} />
              <Route path="/admin" element={<AdminPage />} />
            </Routes>
          </div>

          <aside className="app-aside">
            <HealthCard />
            <SettingsPanel />
          </aside>
        </main>
      </div>
    </BrowserRouter>
  )
}
