import {
  api,
  type ActionExecuteResponse,
  type CheckItem,
  type ClientEvent,
  type ClientLoginRequest,
  type ClientStatusSnapshot,
  type GuardConfigResponse,
  type GuardDefinition,
  type ListResponse,
} from '@vigil/shared'

export const clientApi = {
  login: (payload: ClientLoginRequest) =>
    api.post<{ ok?: boolean }>('/api/login', payload, { skipAuthRedirect: true }),

  status: () => api.get<ClientStatusSnapshot>('/api/status'),

  checks: () => api.get<ListResponse<CheckItem>>('/api/checks'),

  guards: () => api.get<GuardConfigResponse>('/api/guards'),

  saveGuards: (guards: GuardDefinition[]) =>
    api.put<GuardConfigResponse>('/api/guards', { guards }),

  events: (limit = 100) => api.get<ListResponse<ClientEvent>>('/api/events', { query: { limit } }),

  runAction: (name: string, target?: string) =>
    api.post<ActionExecuteResponse>(
      `/api/actions/${encodeURIComponent(name)}`,
      target ? { target } : undefined,
    ),
}
