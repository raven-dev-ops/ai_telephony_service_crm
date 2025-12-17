export function AdminPage() {
  return (
    <section className="stack">
      <h1>Admin</h1>
      <p className="muted">
        Admin APIs require <code>X-Admin-API-Key</code>.
      </p>

      <div className="card">
        <h2>Next</h2>
        <ul className="list">
          <li>Port tenant management + key rotation flows.</li>
          <li>Add security/health dashboards and log views.</li>
        </ul>
      </div>
    </section>
  )
}

