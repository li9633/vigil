/**
 * Vigil v1 前后端共享类型。
 * 字段命名与设计文档第 4/5 章的协议保持一致，后端暂未实现的字段标记为可选。
 */

/** 统一状态色语义：ok / warn / critical / unknown */
export type StatusLevel = 'ok' | 'warn' | 'critical' | 'unknown'

/** 客户端判定状态（4.2 / 4.3） */
export type ClientState =
  | 'HEALTHY'
  | 'SUSPECT'
  | 'DOWN'
  | 'RECOVERED'
  | 'FLAPPING'
  | 'AGENT_DOWN'
  | 'HOST_OR_NET_DOWN'
  | 'BEACON_DOWN'
  | 'DEVICE_REBOOT'
  | 'UNKNOWN'

/** 客户端连接生命周期，与健康状态（state）独立 */
export type ClientLifecycle = 'ONLINE' | 'OFFLINE' | 'REVOKED'

/** 告警（incident）状态：服务端状态机使用 SUSPECT / DOWN / RECOVERED */
export type IncidentState =
  | 'SUSPECT'
  | 'DOWN'
  | 'OPEN'
  | 'ACKED'
  | 'RECOVERED'
  | 'FLAPPING'
  | 'CLOSED'

/** 心跳中的检测项快照（5.6） */
export interface HeartbeatCheck {
  status: StatusLevel | string
  latency_ms?: number | null
  evidence?: Record<string, unknown> | null
}

/** 服务端客户端列表项（clients 表） */
export interface Client {
  client_id: string
  name: string
  model?: string | null
  fw_version?: string | null
  agent_version?: string | null
  last_seen?: string | null
  last_beacon?: string | null
  boot_id?: string | null
  wan_ip?: string | null
  enabled: boolean
  lifecycle?: ClientLifecycle
  state?: ClientState
  revoked_at?: string | null
  revoked_reason?: string | null
  last_incident?: Incident | null
  created_at?: string | null
}

/** 告警记录（incidents 表） */
export interface Incident {
  id: number | string
  client_id: string
  client_name?: string | null
  category: string
  state: IncidentState
  started_at: string
  detected_at?: string | null
  recovered_at?: string | null
  notified_at?: string | null
  acked_at?: string | null
  acked_by?: string | null
  summary?: string | null
  evidence?: Record<string, unknown> | null
  created_at?: string | null
}

/** 告警时间线事件（incident_events 表） */
export interface IncidentEvent {
  incident_id: number | string
  ts: string
  type: string
  payload?: Record<string, unknown> | null
}

/** 服务端事件（events 上报） */
export interface Event {
  event_id: string
  client_id: string
  client_name?: string | null
  ts: string
  type: string
  module?: string | null
  level?: StatusLevel | string
  message?: string | null
  uploaded?: boolean
  payload?: Record<string, unknown> | null
}

/** 动作执行记录（actions 表） */
export interface ActionRecord {
  id: number | string
  client_id?: string | null
  incident_id?: number | string | null
  module: string
  action: string
  risk: string
  started_at: string
  finished_at?: string | null
  result?: string | null
  exit_code?: number | null
  output_trunc?: string | null
}

/** 单个状态指标点（用于 ECharts） */
export interface MetricPoint {
  ts: string
  value: number | null
}

/** 心跳采样点（客户端详情图表） */
export interface HeartbeatPoint {
  ts: string
  seq?: number | null
  latency_ms?: number | null
  checks?: Record<string, HeartbeatCheck>
}

/** 检测项最近结果（客户端详情） */
export interface CheckStatusItem {
  check_id: string
  name?: string | null
  probe?: string | null
  status?: StatusLevel | string | null
  latency_ms?: number | null
  evidence?: Record<string, unknown> | null
}

/** 总览页数据（GET /api/v1/admin/overview） */
export interface OverviewTimelineItem {
  id?: string | number
  ts: string
  type: string
  severity?: StatusLevel | string
  client_id?: string | null
  client_name?: string | null
  summary?: string | null
}

export interface Overview {
  clients_total: number
  clients_online: number
  clients_down: number
  open_incidents: number
  incidents_24h: number
  flapping_clients?: number
  notification_success_rate?: number | null
  timeline: OverviewTimelineItem[]
  generated_at?: string | null
}

/** 客户端详情（GET /api/v1/admin/clients/{id}） */
export interface ClientDetail {
  client: Client
  checks: CheckStatusItem[]
  guards?: GuardSnapshot[]
  heartbeats: HeartbeatPoint[]
  incidents: Incident[]
  recent_events: Event[]
  actions?: ActionRecord[]
  generated_at?: string | null
}

/** 登录（服务端 UI） */
export interface AdminLoginRequest {
  username: string
  password: string
}

export interface AdminLoginResponse {
  ok?: boolean
  actor?: string
  username?: string
  server_time?: number
}

/** 通知渠道配置（notifications 表 / 通知配置页面） */
export type NotificationChannelKind = 'feishu' | 'wecom' | 'email'

/** 邮件连接方式 */
export type EmailSecurity = 'ssl' | 'starttls' | 'none'

export interface NotificationChannel {
  enabled: boolean
  webhook?: string | null
  username?: string | null
  password?: string | null
  smtp_host?: string | null
  smtp_port?: number | null
  from?: string | null
  to?: string | string[] | null
  security?: EmailSecurity | string | null
}

/** down / recovery / test / flapping / system */
export type NotificationKind = 'down' | 'recovery' | 'test' | 'flapping' | 'system'

/** pending / sent / failed / skipped / dead */
export type NotificationStatus = 'pending' | 'sent' | 'failed' | 'skipped' | 'dead'

export interface NotificationRecord {
  id: number | string
  incident_id?: number | string | null
  client_id?: string | null
  channel: NotificationChannelKind | string
  target: string
  kind?: NotificationKind | string
  status: NotificationStatus | string
  attempts: number
  last_error?: string | null
  next_attempt_at?: string | null
  created_at?: string | null
  sent_at?: string | null
}

export interface NotificationSettings {
  feishu: NotificationChannel
  wecom: NotificationChannel
  email: NotificationChannel
  templates?: Record<string, string>
  rate_limit_per_hour?: number
}

/** GET /api/v1/admin/notifications */
export interface NotificationSettingsResponse {
  config: NotificationSettings
  records: NotificationRecord[]
}

/** POST /api/v1/admin/notifications/test */
export interface NotificationTestResult {
  channel: NotificationChannelKind | string
  ok: boolean
  error?: string | null
}

/** 策略配置（settings 表） */
export interface Policies {
  heartbeat_suspect_seconds?: number
  heartbeat_down_seconds?: number
  beacon_down_seconds?: number
  flapping_window_minutes?: number
  flapping_threshold?: number
  action_cooldown_seconds?: number
  action_max_per_hour?: number
  maintenance_window?: string | null
}

/** 审计日志 */
export interface AuditLog {
  actor: string
  action: string
  target?: string | null
  ts: string
  detail?: string | null
}

/** 列表响应：兼容裸数组与分页包装 */
export type ListResponse<T> = T[] | { items: T[]; total?: number; page?: number; page_size?: number }

/* ------------------------------------------------------------------ */
/* 客户端 UI（agent 内置 HTTP 服务，默认 :8787）                        */
/* ------------------------------------------------------------------ */

/** 客户端首页状态（GET /api/status） */
export interface ClientStatusSnapshot {
  state?: ClientState | string
  agent_version?: string | null
  uptime?: number | null
  boot_id?: string | null
  wan_ip?: string | null
  lan_ip?: string | null
  carrier?: string | null
  pppoe_uptime?: number | null
  server_connected?: boolean | null
  last_heartbeat_at?: string | null
  last_beacon_at?: string | null
  guards?: GuardSnapshot[]
  guards_source?: 'local' | 'server' | string | null
  guards_editable?: boolean | null
  latest_checks?: CheckItem[]
  spool?: { pending: number; dropped: number }
  maintenance?: boolean
  generated_at?: string | null
}

/** 单个 Check 的检测结果（Guard 内） */
export interface GuardCheckResult {
  id: string
  probe?: string | null
  status: StatusLevel | string
  latency_ms?: number | null
  evidence?: Record<string, unknown> | null
}

/** 守护项（Guard）运行时快照 */
export interface GuardSnapshot {
  id: string
  name?: string | null
  template?: string | null
  /** OK / PENDING / FAILED / RECOVERING / GAVE_UP / DISABLED */
  status?: string | null
  /** 聚合后的检测状态：ok / warn / critical / unknown / skipped */
  check_status?: StatusLevel | string | null
  failure_count?: number | null
  success_count?: number | null
  recovery_rounds?: number | null
  rearm_at?: number | null
  checks?: Record<string, GuardCheckResult> | null
}

/** 检测项配置（服务端下发 / 客户端本地） */
export interface CheckDefinition {
  id: string
  name: string
  probe: string
  params?: Record<string, unknown>
  expect?: Record<string, unknown>
  timeout?: number | null
  depends_on?: string[]
  enabled?: boolean
}

/** GuardEditor 可选的检测项（只依赖标识与依赖关系） */
export interface CheckOption {
  id: string
  name?: string | null
  probe?: string | null
  enabled?: boolean
  depends_on?: string[]
}

/** GET/PUT /api/v1/admin/clients/{id}/checks */
export interface ClientChecksResponse {
  items: CheckDefinition[]
  managed: boolean
}

/** 触发策略 */
export interface GuardPolicy {
  interval?: number
  fail_threshold?: number
  fail_mode?: 'consecutive' | 'window' | string
  fail_window?: number
  success_threshold?: number
  cooldown?: number
  max_actions_per_hour?: number
  gave_up_after?: number
  backoff?: string
}

/** 恢复动作链中的一步 */
export interface GuardRecoveryStep {
  action: string
  target?: string | null
  verify_after?: number | null
  continue_on_failure?: boolean
}

/** 可编辑的守护项定义（本地配置 / 服务端下发） */
export interface GuardDefinition {
  id: string
  name: string
  template?: string | null
  enabled?: boolean
  check_refs: string[]
  policy?: GuardPolicy
  recovery?: GuardRecoveryStep[]
  limits?: Record<string, unknown>
  evaluate?: Record<string, unknown>
}

/** GET/PUT /api/guards（客户端本地） */
export interface GuardConfigResponse {
  items: GuardDefinition[]
  source: 'local' | 'server' | string
  editable: boolean
}

/** GET/PUT/DELETE /api/v1/admin/clients/{id}/guards（服务端） */
export interface ClientGuardsResponse {
  items: GuardDefinition[]
  managed: boolean
}

/** 检测项（GET /api/checks：定义 + 最近结果） */
export interface CheckItem {
  id: string
  name?: string | null
  layer?: string | null
  status: StatusLevel
  latency_ms?: number | null
  interval?: number | null
  enabled?: boolean
  evidence?: string | null
  updated_at?: string | null
  probe?: string | null
  params?: Record<string, unknown>
  expect?: Record<string, unknown>
  timeout?: number | null
  depends_on?: string[]
  /** 引用的 probe 不在当前目录中 */
  probe_missing?: boolean
  /** 是否已有最近一次检测结果 */
  has_result?: boolean
}

/** 检测项试运行（POST /api/checks/{id}/test） */
export interface CheckTestResponse {
  check: CheckItem | Record<string, unknown>
  result: {
    check_id?: string
    status: StatusLevel | string
    latency_ms?: number | null
    evidence?: Record<string, unknown> | null
  }
  status: StatusLevel | string
  latency_ms?: number | null
  evidence: Record<string, unknown>
}

/** 客户端本地事件（GET /api/events） */
export interface ClientEvent {
  event_id: string
  ts: string
  type: string
  module?: string | null
  level?: StatusLevel | string
  message?: string | null
  uploaded?: boolean
  payload?: Record<string, unknown> | null
}

/** 手动动作（L1 白名单，POST /api/actions/{name}） */
export interface ActionOption {
  name: string
  label: string
  description?: string
  risk: 'L1'
  /** 需要 target 的动作（如 restart_service 的 dnsmasq） */
  target?: string
}

export interface ActionExecuteResponse {
  ok?: boolean
  action?: string
  started_at?: string | null
  finished_at?: string | null
  result?: string | null
  exit_code?: number | null
  output?: string | null
  cooldown_seconds?: number | null
  cooldown_remaining?: number | null
  message?: string | null
}

/** 探测器参数描述（manifest.params_schema） */
export interface ProbeParamSpec {
  type?: 'string' | 'number' | 'integer' | 'boolean' | 'enum' | 'array' | 'object' | string
  required?: boolean
  default?: unknown
  label?: string
  placeholder?: string
  values?: Array<string | number>
  items?: string
}

/** 探测器目录项（内置 / 本地） */
export interface ProbeCatalogItem {
  id: string
  name?: string | null
  description?: string | null
  version?: string | null
  source?: 'builtin' | 'local' | string
  remote_allowed?: boolean
  params_schema?: Record<string, ProbeParamSpec>
  default_expect?: Record<string, unknown>
  default_timeout?: number | null
  valid?: boolean
  error?: string | null
  entry?: string | null
  interpreter?: string | null
  sha256?: string | null
}

/** GET /api/probes（服务端为最近心跳上报的只读目录） */
export interface ProbeCatalogResponse {
  items: ProbeCatalogItem[]
  directory?: string
  updated_at?: string | null
}

/** POST /api/probes/{id}/test */
export interface ProbeTestResponse {
  probe: ProbeCatalogItem
  params: Record<string, unknown>
  expect: Record<string, unknown>
  status: StatusLevel | string
  evidence: Record<string, unknown>
  observation: {
    data?: Record<string, unknown>
    latency_ms?: number | null
    error?: string | null
  }
  latency_ms?: number | null
}

/** 客户端结构化日志（GET /api/logs） */
export interface LogGuardRef {
  id?: string
  name?: string
  template?: string
}

export interface LogEntry {
  v?: number
  ts: string
  uptime_ms?: number
  seq?: number
  level: string
  origin?: string
  event: string
  logger?: string
  msg: string
  client_id?: string
  boot_id?: string
  trace_id?: string
  recovery_id?: string
  guard?: LogGuardRef
  check?: { id?: string; probe?: string }
  action?: {
    name?: string
    target?: string | null
    result?: string | null
    exit_code?: number | null
  }
  status?: string
  from?: string
  to?: string
  check_status?: string
  failure_count?: number
  recovered?: boolean
  skipped?: boolean
  duration_ms?: number | null
  attempt?: number
  suppressed?: number
  evidence?: unknown
  err?: { type?: string; stack?: string }
  [key: string]: unknown
}

/** 日志过滤条件 */
export interface LogQuery {
  limit?: number
  level?: string
  event?: string
  guard_id?: string
  recovery_id?: string
  since?: number
}

/** 服务端结构化日志（GET /api/v1/admin/logs） */
export interface ServerLogEntry {
  v?: number
  ts: string
  uptime_ms?: number
  seq?: number
  level: string
  origin?: string
  instance?: string
  event: string
  logger?: string
  msg: string
  request_id?: string
  actor?: string
  client_id?: string
  incident_id?: number | string
  guard_id?: string
  guard_name?: string
  method?: string
  path?: string
  status?: number
  duration_ms?: number | null
  channel?: string
  kind?: string
  evidence?: unknown
  err?: { type?: string; stack?: string }
  [key: string]: unknown
}

export interface ServerLogQuery {
  limit?: number
  level?: string
  event?: string
  client_id?: string
  request_id?: string
  instance?: string
  since?: number
}

/** 服务端审计日志（GET /api/v1/admin/audit） */
export interface AuditRecord {
  id: number
  actor: string
  action: string
  target: string
  ts: string
  detail: string
}

export interface AuditQuery {
  actor?: string
  action?: string
  target?: string
  since?: number
  until?: number
  limit?: number
  offset?: number
}

export interface AuditResponse {
  items: AuditRecord[]
  total: number
}

/** 运行时策略配置（GET/PUT /api/v1/admin/policies） */
export interface PolicySettings {
  heartbeat_interval: number
  suspect_seconds: number
  down_seconds: number
  beacon_seconds: number
  flapping_window_seconds: number
  flapping_threshold: number
  actions_enabled: boolean
  action_cooldown_seconds: number
  action_max_per_hour: number
  retention_days: number
  disk_alert_percent: number
  disk_alert_cooldown_seconds: number
}

export interface PolicyResponse {
  policies: PolicySettings
  defaults: PolicySettings
}

/** 系统设置（GET /api/v1/admin/system） */
export interface AdminSystemSettings {
  admin_username: string
  password_updated_at?: string | null
  retention_days: number
  disk_alert_percent: number
  disk_alert_cooldown_seconds: number
}

export interface RotateSecretResponse {
  client_id: string
  /** 明文 secret，仅在轮换响应中返回一次 */
  secret: string
  rotated_at: string
}

/** 客户端本地设置 */
export interface ClientSettings {
  server_base_url?: string | null
  heartbeat_interval?: number | null
  detect?: Record<string, unknown>
  action?: Record<string, unknown>
  maintenance_window?: string | null
  version?: string | null
}

/** 客户端配对状态（GET /api/settings → pairing） */
export interface ClientPairingState {
  status: 'idle' | 'pending' | 'code_expired' | 'paired' | 'denied' | 'expired' | string
  code: string
  code_expires_at: number
  expires_at: number
  last_error: string
  client_id?: string
  server_url?: string
}

/** 客户端取消注册结果（POST /api/unregister） */
export interface ClientUnregisterResponse {
  settings: ClientSettingsSnapshot
  server_notified: boolean
  server_error: string
}

/** 客户端运行设置快照（GET /api/settings） */
export interface ClientSettingsSnapshot {
  version: string
  protocol_version?: number | string
  config_path?: string
  server: {
    base_url: string
    client_id?: string
    registered: boolean
    heartbeat_interval: number
    config_poll_interval: number
    verify_tls: boolean
  }
  pairing?: ClientPairingState
  action_safety: {
    global_enabled: boolean
    max_risk_level: string
    max_actions_per_hour: number
    allowed_services: string[]
  }
  maintenance: {
    active: boolean
    until: number
    remaining_seconds: number
  }
  guards_source?: string
  generated_at?: number
}

/** 客户端设置更新请求（PUT /api/settings） */
export interface ClientSettingsUpdate {
  server?: { base_url: string }
  heartbeat_interval?: number
  action_safety?: {
    global_enabled?: boolean
    max_risk_level?: string
    max_actions_per_hour?: number
  }
  maintenance_minutes?: number
}

/** 客户端登录（POST /api/login） */
export interface ClientLoginRequest {
  token: string
}

/** 服务端待审批配对请求 */
export interface PairingRequest {
  request_id: string
  instance_id: string
  name: string
  model: string
  fw_version: string
  agent_version: string
  client_ip: string
  status: string
  attempts: number
  client_id: string
  created_at: string | null
  expires_at: string | null
  code_expires_at: string | null
  approved_at: string | null
  approved_by: string | null
  claimed_at: string | null
  denied_at: string | null
  denied_reason: string | null
}

export interface PairingListResponse {
  items: PairingRequest[]
}