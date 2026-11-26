import {
  api,
  getApiConfig,
  type ActionExecuteResponse,
  type CheckItem,
  type ClientEvent,
  type ClientLoginRequest,
  type ClientStatusSnapshot,
  type ClientSettingsSnapshot,
  type ClientSettingsUpdate,
  type GuardConfigResponse,
  type GuardDefinition,
  type ListResponse,
  type LogEntry,
  type LogQuery,
} from '@vigil/shared'

export const clientApi = {
  login: (payload: ClientLoginRequest) =>
    api.post<{ ok?: boolean }>('/api/login', payload, { skipAuthRedirect: true }),

  status: () => api.get<ClientStatusSnapshot>('/api/status'),

  checks: () => api.get<ListResponse<CheckItem>>('/api/checks'),

  guards: () => api.get<GuardConfigResponse>('/api/guards'),

  saveGuards: (guards: GuardDefinition[]) =>
    api.put<GuardConfigResponse>('/api/guards', { guards }),

  settings: () => api.get<ClientSettingsSnapshot>('/api/settings'),

  saveSettings: (payload: ClientSettingsUpdate) =>
    api.put<ClientSettingsSnapshot>('/api/settings', payload),

  events: (limit = 100) => api.get<ListResponse<ClientEvent>>('/api/events', { query: { limit } }),

  logs: (query: LogQuery = {}) =>
    api.get<ListResponse<LogEntry>>('/api/logs', { query: { ...query } }),

  logsExportUrl: (query: LogQuery = {}) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '') {
        params.set(key, String(value))
      }
    }
    const suffix = params.toString()
    return `${getApiConfig().baseURL}/api/logs/export${suffix ? `?${suffix}` : ''}`
  },

  runAction: (name: string, target?: string) =>
    api.post<ActionExecuteResponse>(
      `/api/actions/${encodeURIComponent(name)}`,
      target ? { target } : undefined,
    ),
}
