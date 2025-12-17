export type RequestMode = 'public' | 'owner' | 'admin' | 'widget'

export type ApiClientSettings = {
  apiBaseUrl: string
  businessId: string
  apiKey: string
  ownerToken: string
  adminApiKey: string
  widgetToken: string
}

export function apiUrl(path: string, apiBaseUrl: string): string {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  const base = apiBaseUrl.trim()

  if (!base) return normalizedPath
  if (base.startsWith('http://') || base.startsWith('https://')) {
    return new URL(normalizedPath, base).toString()
  }

  return `${base.replace(/\/$/, '')}${normalizedPath}`
}

function buildHeaders(settings: ApiClientSettings, mode: RequestMode): Headers {
  const headers = new Headers()

  if (settings.businessId) headers.set('X-Business-ID', settings.businessId)

  if (mode === 'owner') {
    if (settings.apiKey) headers.set('X-API-Key', settings.apiKey)
    if (settings.ownerToken) headers.set('X-Owner-Token', settings.ownerToken)
  }

  if (mode === 'admin') {
    if (settings.adminApiKey) headers.set('X-Admin-API-Key', settings.adminApiKey)
  }

  if (mode === 'widget') {
    if (settings.widgetToken) headers.set('X-Widget-Token', settings.widgetToken)
  }

  return headers
}

function mergeHeaders(headers: Headers, incoming?: HeadersInit): void {
  if (!incoming) return
  if (incoming instanceof Headers) {
    for (const [key, value] of incoming.entries()) {
      headers.set(key, value)
    }
    return
  }
  if (Array.isArray(incoming)) {
    for (const [key, value] of incoming) {
      headers.set(key, value)
    }
    return
  }
  for (const [key, value] of Object.entries(incoming)) {
    if (value == null) continue
    headers.set(key, String(value))
  }
}

export async function fetchText(
  path: string,
  settings: ApiClientSettings,
  mode: RequestMode,
  init?: RequestInit,
): Promise<{ ok: boolean; status: number; statusText: string; text: string }> {
  const url = apiUrl(path, settings.apiBaseUrl)
  const headers = buildHeaders(settings, mode)
  mergeHeaders(headers, init?.headers)

  const resp = await fetch(url, {
    ...init,
    headers,
  })
  const text = await resp.text()

  return {
    ok: resp.ok,
    status: resp.status,
    statusText: resp.statusText,
    text,
  }
}

