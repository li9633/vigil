import {
  api,
  getApiConfig,
  type ActionExecuteResponse,
  type CheckDefinition,
  type CheckItem,
  type CheckTestResponse,
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
  type ProbeCatalogResponse,
  type ProbeTestResponse,
} from '@vigil/shared'

export const clientApi = {
  login: (payload: ClientLoginRequest) =>
    api.post<{ ok?: boolean }>('/api/login', payload, { skipAuthRedirect: true }),

  status: () => api.get<ClientStatusSnapshot>('/api/status'),

  checks: () => api.get<ListResponse<CheckItem>>('/api/checks'),

  saveChecks: (checks: CheckDefinition[]) =>
    api.put<ListResponse<CheckItem>>('/api/checks', { checks }),

  testCheck: (id: string) =>
    api.post<CheckTestResponse>(`/api/checks/${encodeURIComponent(id)}/test`, {}, { timeoutMs: 40000 }),

  probes: () => api.get<ProbeCatalogResponse>('/api/probes'),

  installProbe: (file: File) =>
    api.post<ProbeCatalogResponse>('/api/probes', file, {
      headers: { 'Content-Type': 'application/gzip' },
      timeoutMs: 60000,
    }),

  deleteProbe: (id: string) =>
    api.delete<ProbeCatalogResponse>(`/api/probes/${encodeURIComponent(id)}`),

  testProbe: (id: string, params: Record<string, unknown>, expect?: Record<string, unknown>) =>
    api.post<ProbeTestResponse>(
      `/api/probes/${encodeURIComponent(id)}/test`,
      { params, expect },
      { timeoutMs: 40000 },
    ),

  guards: () => api.get<GuardConfigResponse>('/api/guards'),

  saveGuards: (guards: GuardDefinition[]) =>
    api.put<GuardConfigResponse>('/api/guards', { guards }),

  settings: () => api.get<ClientSettingsSnapshot>('/api/settings'),

  saveSettings: (payload: ClientSettingsUpdate) =>
    api.put<ClientSettingsSnapshot>('/api/settings', payload),

  startPairing: (baseUrl: string) =>
    api.post<ClientSettingsSnapshot>('/api/pairing', { base_url: baseUrl }, { timeoutMs: 20000 }),

  pollPairing: () =>
    api.post<ClientSettingsSnapshot>('/api/pairing/poll', {}, { timeoutMs: 20000 }),

  cancelPairing: () => api.delete<ClientSettingsSnapshot>('/api/pairing'),

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
