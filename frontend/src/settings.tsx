import { createContext, useContext, useEffect, useMemo, useState } from 'react'

export type AppSettings = {
  apiBaseUrl: string
  businessId: string
  apiKey: string
  ownerToken: string
  adminApiKey: string
  widgetToken: string
}

const SETTINGS_STORAGE_KEY = 'raven_crm_settings_v1'

const defaultSettings: AppSettings = {
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL?.trim() ?? '',
  businessId: 'default_business',
  apiKey: '',
  ownerToken: '',
  adminApiKey: '',
  widgetToken: '',
}

function loadSettings(): AppSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_STORAGE_KEY)
    if (!raw) return defaultSettings
    const parsed = JSON.parse(raw) as Partial<AppSettings>
    return { ...defaultSettings, ...parsed }
  } catch {
    return defaultSettings
  }
}

function saveSettings(settings: AppSettings): void {
  localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings))
}

type SettingsContextValue = {
  settings: AppSettings
  updateSettings: (patch: Partial<AppSettings>) => void
  resetSettings: () => void
}

const SettingsContext = createContext<SettingsContextValue | null>(null)

export function SettingsProvider({ children }: { children: React.ReactNode }) {
  const [settings, setSettings] = useState<AppSettings>(() => loadSettings())

  const updateSettings = (patch: Partial<AppSettings>) => {
    setSettings((current) => ({ ...current, ...patch }))
  }

  const resetSettings = () => {
    setSettings(defaultSettings)
  }

  useEffect(() => {
    saveSettings(settings)
  }, [settings])

  const value = useMemo(
    () => ({ settings, updateSettings, resetSettings }),
    [settings],
  )

  return <SettingsContext value={value}>{children}</SettingsContext>
}

export function useSettings(): SettingsContextValue {
  const value = useContext(SettingsContext)
  if (!value) {
    throw new Error('useSettings must be used within SettingsProvider')
  }
  return value
}

