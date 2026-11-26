import { api, getApiConfig, type AdminLoginRequest, type AdminLoginResponse, type AdminSystemSettings, type AuditQuery, type AuditResponse, type Client, type ClientDetail, type ClientGuardsResponse, type GuardDefinition, type Incident, type ListResponse, type NotificationRecord, type NotificationSettings, type NotificationSettingsResponse, type NotificationTestResult, type Overview, type PolicyResponse, type PolicySettings, type RotateSecretResponse, type ServerLogEntry, type ServerLogQuery } from '@vigil/shared'

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

  logs: (query: ServerLogQuery = {}) =>
    api.get<ListResponse<ServerLogEntry>>('/api/v1/admin/logs', { query: { ...query } }),

  audit: (query: AuditQuery = {}) =>
    api.get<AuditResponse>('/api/v1/admin/audit', { query: { ...query } }),

  policies: () => api.get<PolicyResponse>('/api/v1/admin/policies'),

  savePolicies: (policies: Partial<PolicySettings>) =>
    api.put<PolicyResponse>('/api/v1/admin/policies', policies),

  system: () => api.get<AdminSystemSettings>('/api/v1/admin/system'),

  changeAdminPassword: (payload: { current_password: string; new_password: string }) =>
    api.put<{ ok: boolean; password_updated_at: string | null }>(
      '/api/v1/admin/system/admin-password',
      payload,
    ),

  rotateClientSecret: (id: string) =>
    api.post<RotateSecretResponse>(
      `/api/v1/admin/clients/${encodeURIComponent(id)}/secret/rotate`,
    ),

  auditExportUrl: (query: AuditQuery = {}) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '') {
        params.set(key, String(value))
      }
    }
    const suffix = params.toString()
    return `${getApiConfig().baseURL}/api/v1/admin/audit/export${suffix ? `?${suffix}` : ''}`
  },

  logsExportUrl: (query: ServerLogQuery = {}) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '') {
        params.set(key, String(value))
      }
    }
    const suffix = params.toString()
    return `${getApiConfig().baseURL}/api/v1/admin/logs/export${suffix ? `?${suffix}` : ''}`
  },
}
