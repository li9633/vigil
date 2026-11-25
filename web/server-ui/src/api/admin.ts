import { api, type AdminLoginRequest, type AdminLoginResponse, type Client, type ClientDetail, type ClientGuardsResponse, type GuardDefinition, type Incident, type ListResponse, type NotificationRecord, type NotificationSettings, type NotificationSettingsResponse, type NotificationTestResult, type Overview } from '@vigil/shared'

export interface IncidentQuery {
  state?: string
  category?: string
  client_id?: string
  limit?: number
}

export const adminApi = {
  login: (payload: AdminLoginRequest) =>
    api.post<AdminLoginResponse>('/api/v1/admin/login', payload, { skipAuthRedirect: true }),

  overview: () => api.get<Overview>('/api/v1/admin/overview'),

  clients: () => api.get<ListResponse<Client>>('/api/v1/admin/clients'),

  client: (id: string) =>
    api.get<ClientDetail>(`/api/v1/admin/clients/${encodeURIComponent(id)}`),

  clientGuards: (id: string) =>
    api.get<ClientGuardsResponse>(`/api/v1/admin/clients/${encodeURIComponent(id)}/guards`),

  saveClientGuards: (id: string, guards: GuardDefinition[]) =>
    api.put<ClientGuardsResponse>(`/api/v1/admin/clients/${encodeURIComponent(id)}/guards`, {
      guards,
    }),

  clearClientGuards: (id: string) =>
    api.delete<ClientGuardsResponse>(`/api/v1/admin/clients/${encodeURIComponent(id)}/guards`),

  incidents: (query: IncidentQuery = {}) =>
    api.get<ListResponse<Incident>>('/api/v1/admin/incidents', {
      query: {
        state: query.state,
        category: query.category,
        client_id: query.client_id,
        limit: query.limit,
      },
    }),

  ackIncident: (id: string) =>
    api.post<Incident>(`/api/v1/admin/incidents/${encodeURIComponent(id)}/ack`),

  notifications: () => api.get<NotificationSettingsResponse>('/api/v1/admin/notifications'),

  saveNotifications: (payload: NotificationSettings) =>
    api.put<{ config: NotificationSettings }>('/api/v1/admin/notifications', payload),

  testNotification: (channel: string) =>
    api.post<{ results: NotificationTestResult[] }>('/api/v1/admin/notifications/test', {
      channel,
    }),

  notificationRecords: (limit = 100) =>
    api.get<{ records: NotificationRecord[] }>('/api/v1/admin/notifications/records', {
      query: { limit },
    }),
}
