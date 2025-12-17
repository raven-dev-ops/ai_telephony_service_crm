import { useSettings } from '../settings'

export function SettingsPanel() {
  const { settings, updateSettings, resetSettings } = useSettings()

  return (
    <section className="card">
      <h2>Settings</h2>
      <div className="form-grid">
        <label>
          API Base URL
          <input
            value={settings.apiBaseUrl}
            onChange={(e) => updateSettings({ apiBaseUrl: e.target.value })}
            placeholder="(blank = use Vite proxy)"
            spellCheck={false}
          />
        </label>

        <label>
          Business ID
          <input
            value={settings.businessId}
            onChange={(e) => updateSettings({ businessId: e.target.value })}
            placeholder="default_business"
            spellCheck={false}
          />
        </label>

        <label>
          X-API-Key
          <input
            value={settings.apiKey}
            onChange={(e) => updateSettings({ apiKey: e.target.value })}
            placeholder="tenant API key"
            spellCheck={false}
          />
        </label>

        <label>
          X-Owner-Token
          <input
            value={settings.ownerToken}
            onChange={(e) => updateSettings({ ownerToken: e.target.value })}
            placeholder="owner/dashboard token"
            spellCheck={false}
          />
        </label>

        <label>
          X-Admin-API-Key
          <input
            value={settings.adminApiKey}
            onChange={(e) => updateSettings({ adminApiKey: e.target.value })}
            placeholder="admin API key"
            spellCheck={false}
          />
        </label>

        <label>
          X-Widget-Token
          <input
            value={settings.widgetToken}
            onChange={(e) => updateSettings({ widgetToken: e.target.value })}
            placeholder="widget token"
            spellCheck={false}
          />
        </label>
      </div>

      <div className="button-row">
        <button type="button" onClick={resetSettings}>
          Reset
        </button>
      </div>
    </section>
  )
}

