import {
  api,
  type ActionExecuteResponse,
  type CheckItem,
  type ClientEvent,
  type ClientLoginRequest,
  type ClientStatusSnapshot,
  type ListResponse,
} from '@vigil/shared'

export const clientApi = {
  login: (payload: ClientLoginRequest) =>
    api.post<{ ok?: boolean }>('/api/login', payload, { skipAuthRedirect: true }),

  status: () => api.get<ClientStatusSnapshot>('/api/status'),

  checks: () => api.get<ListResponse<CheckItem>>('/api/checks'),

  events: (limit = 100) => api.get<ListResponse<ClientEvent>>('/api/events', { query: { limit } }),

  runAction: (name: string, target?: string) =>
    api.post<ActionExecuteResponse>(
      `/api/actions/${encodeURIComponent(name)}`,
      target ? { target } : undefined,
    ),
}
