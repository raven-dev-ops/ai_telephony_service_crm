import { useState } from 'react'

import { fetchText } from '../api'
import { useSettings } from '../settings'

type HealthState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ok'; detail: string }
  | { status: 'error'; detail: string }

export function HealthCard() {
  const { settings } = useSettings()
  const [state, setState] = useState<HealthState>({ status: 'idle' })

  const run = async () => {
    setState({ status: 'loading' })
    try {
      const result = await fetchText('/healthz', settings, 'public')
      if (!result.ok) {
        setState({
          status: 'error',
          detail: `${result.status} ${result.statusText}: ${result.text.slice(0, 300)}`,
        })
        return
      }
      setState({ status: 'ok', detail: result.text.slice(0, 300) || 'ok' })
    } catch (error) {
      setState({
        status: 'error',
        detail: error instanceof Error ? error.message : String(error),
      })
    }
  }

  return (
    <section className="card">
      <div className="card-header">
        <h2>Backend Health</h2>
        <button type="button" onClick={run} disabled={state.status === 'loading'}>
          {state.status === 'loading' ? 'Checking…' : 'Check'}
        </button>
      </div>

      {state.status === 'idle' && (
        <p className="muted">
          Uses <code>/healthz</code>.
        </p>
      )}
      {state.status === 'ok' && <pre className="code-block">{state.detail}</pre>}
      {state.status === 'error' && (
        <pre className="code-block error">{state.detail}</pre>
      )}
    </section>
  )
}

