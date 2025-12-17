import { useSettings } from '../settings'

export function HomePage() {
  const { settings } = useSettings()

  return (
    <section className="stack">
      <h1>TypeScript Frontend</h1>
      <p className="muted">
        Scaffolded with Vite + React + TypeScript. Use the Settings panel to
        store auth headers in localStorage.
      </p>

      <div className="card">
        <h2>Quick Start</h2>
        <ol className="list">
          <li>
            Start the backend: <code>cd backend</code> then{' '}
            <code>uvicorn app.main:app --reload</code>
          </li>
          <li>
            Start the frontend: <code>cd frontend</code> then{' '}
            <code>npm run dev</code>
          </li>
          <li>
            Leave API Base URL blank to use the Vite dev proxy (recommended for
            local dev).
          </li>
        </ol>
      </div>

      <div className="card">
        <h2>Current Settings</h2>
        <ul className="list">
          <li>
            API Base URL:{' '}
            <code>{settings.apiBaseUrl || '(blank / proxied)'}</code>
          </li>
          <li>
            Business ID: <code>{settings.businessId || '(none)'}</code>
          </li>
        </ul>
      </div>
    </section>
  )
}

