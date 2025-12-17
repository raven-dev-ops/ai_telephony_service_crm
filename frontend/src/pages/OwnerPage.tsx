export function OwnerPage() {
  return (
    <section className="stack">
      <h1>Owner</h1>
      <p className="muted">
        Owner APIs typically require <code>X-API-Key</code> and{' '}
        <code>X-Owner-Token</code>.
      </p>

      <div className="card">
        <h2>Next</h2>
        <ul className="list">
          <li>Rebuild the existing owner dashboard cards in TypeScript.</li>
          <li>Introduce typed API clients + shared UI components.</li>
          <li>
            Replace the static <code>dashboard/index.html</code> when ready.
          </li>
        </ul>
      </div>
    </section>
  )
}

