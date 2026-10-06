import { request } from '../../api'

export type AiStatus = {
  supported: boolean; installed: boolean; enabled: boolean; ready: boolean; reason: string | null
  model: string; backend: 'cuda' | null; can_manage: boolean
  counts: { documents: number; candidates: number; jobs: number }
  model_source?: 'builtin' | 'registered'; parallel_slots?: number; context_tokens_per_slot?: number
  runtime_installed?: boolean; model_available?: boolean

}
export type AiConfig = { enabled: boolean; learning_enabled: boolean; revision: number }
export type AiPrompt = { role: 'mechanic' | 'operator' | 'admin'; content: string; revision: number }
export type AiSource = { id: string; title: string; excerpt: string; trust: 'instruction' | 'experience' | 'unverified' }
export type AiMessage = { id: string; role: 'user' | 'assistant'; content: string; sources: AiSource[]; created_at: string }
export type AiJob = {
  id: string; kind: string; state: 'queued' | 'running' | 'waiting' | 'succeeded' | 'failed' | 'cancelled'
  created_at: string; updated_at: string; error: string | null; result: unknown; actions?: AiAction[]
}
export type AiAction = {
  id: string; tool: string; state: 'ready' | 'waiting' | 'approved' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'uncertain'
  preview: string; arguments: Record<string, unknown> | null; result: unknown; error: string | null
  digest: string | null; expires_at: string | null; created_at: string
}
export type Conversation = { id: string; title: string; park_id: number; issue_key: string | null; updated_at: string }
export type ConversationDetail = Conversation & { messages: AiMessage[]; jobs: AiJob[] }
export type Connector = { id: string; name: string; url: string; method: 'GET' | 'POST' | 'PUT' | 'PATCH'; enabled: boolean; token_set: boolean; revision: number }
export type ConnectorWrite = { name: string; url: string; method: Connector['method']; token?: string; enabled?: boolean }
export type AssistantScript = { id: string; name: string; source: string; revision: number; enabled: boolean; tested_revision: number | null; updated_at: string }
export type AutomationFilters = { component_ids: string[]; defect_codes: string[]; keywords: string[] }
export type AutomationAction = { connector_id?: string; script_id?: string; body: unknown }
export type Automation = { id: string; name: string; park_id: number; enabled: boolean; revision: number; filters: AutomationFilters; action: AutomationAction; updated_at: string }
export type AutomationRun = { id: string; automation_id: string; event_key: string; state: string; error: string | null; created_at: string; updated_at: string; result: unknown }
export type CleanupResult = { deleted: number; conversations_deleted?: number; jobs_deleted?: number; messages_deleted?: number; events_cleaned?: number }

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) })
const mutation = (method: 'POST' | 'PATCH' | 'PUT' | 'DELETE', body?: unknown): RequestInit => ({ method, ...(body === undefined ? {} : json(body)) })
const encoded = (id: string) => encodeURIComponent(id)

export type AssistantApiClient = typeof assistantApi

export const assistantApi = {
  status: (signal?: AbortSignal) => request<AiStatus>('/ai/status', { signal }),
  config: (signal?: AbortSignal) => request<AiConfig>('/ai/config', { signal }),
  updateConfig: (value: AiConfig) => request<AiConfig>('/ai/config', mutation('PATCH', value)),
  runtime: (action: 'install' | 'enable' | 'disable' | 'remove_model') => request<AiStatus>('/ai/runtime', mutation('POST', { action })),
  prompts: (signal?: AbortSignal) => request<AiPrompt[]>('/ai/prompts', { signal }),
  updatePrompt: (role: AiPrompt['role'], content: string, revision: number) => request<AiPrompt>(`/ai/prompts/${role}`, mutation('PUT', { content, revision })),

  conversations: (signal?: AbortSignal) => request<Conversation[]>('/ai/conversations', { signal }),
  createConversation: (value: { title?: string; park_id: number; issue_key?: string }, signal?: AbortSignal) => request<Conversation>('/ai/conversations', { ...mutation('POST', value), signal }),
  conversation: (id: string, signal?: AbortSignal) => request<ConversationDetail>(`/ai/conversations/${encoded(id)}`, { signal }),
  deleteConversation: (id: string) => request<void>(`/ai/conversations/${encoded(id)}`, mutation('DELETE')),
  sendMessage: (conversationId: string, content: string, idempotency_key: string, signal?: AbortSignal) => request<AiJob>(`/ai/conversations/${encoded(conversationId)}/messages`, { ...mutation('POST', { content, idempotency_key, use_tools: true }), signal }),
  job: (id: string, signal?: AbortSignal) => request<AiJob>(`/ai/jobs/${encoded(id)}`, { signal }),
  confirmAction: (id: string, digest: string) => request<AiJob>(`/ai/actions/${encoded(id)}/confirm`, mutation('POST', { digest })),
  cancelJob: (id: string) => request<AiJob>(`/ai/jobs/${encoded(id)}/cancel`, mutation('POST')),

  connectors: (signal?: AbortSignal) => request<Connector[]>('/ai/connectors', { signal }),
  createConnector: (value: ConnectorWrite) => request<Connector>('/ai/connectors', mutation('POST', value)),
  updateConnector: (id: string, value: Partial<ConnectorWrite> & { revision: number }) => request<Connector>(`/ai/connectors/${encoded(id)}`, mutation('PATCH', value)),
  deleteConnector: (id: string) => request<void>(`/ai/connectors/${encoded(id)}`, mutation('DELETE')),
  scripts: (signal?: AbortSignal) => request<AssistantScript[]>('/ai/scripts', { signal }),
  createScript: (value: { name: string; source: string }) => request<AssistantScript>('/ai/scripts', mutation('POST', value)),
  updateScript: (id: string, value: Partial<Pick<AssistantScript, 'name' | 'source' | 'enabled'>> & { revision: number }) => request<AssistantScript>(`/ai/scripts/${encoded(id)}`, mutation('PATCH', value)),
  deleteScript: (id: string) => request<void>(`/ai/scripts/${encoded(id)}`, mutation('DELETE')),
  testScript: (id: string, input: unknown) => request<AiJob>(`/ai/scripts/${encoded(id)}/test`, mutation('POST', { input })),
  createDraft: (value: { kind: 'script' | 'automation'; instruction: string; park_id: number }) => request<AiJob>('/ai/drafts', mutation('POST', value)),
  automations: (signal?: AbortSignal) => request<Automation[]>('/ai/automations', { signal }),
  createAutomation: (value: Omit<Automation, 'id' | 'revision' | 'enabled' | 'updated_at'>) => request<Automation>('/ai/automations', mutation('POST', value)),
  updateAutomation: (id: string, value: Partial<Omit<Automation, 'id' | 'park_id' | 'updated_at'>> & { revision: number }) => request<Automation>(`/ai/automations/${encoded(id)}`, mutation('PATCH', value)),
  deleteAutomation: (id: string) => request<void>(`/ai/automations/${encoded(id)}`, mutation('DELETE')),
  previewAutomation: (id: string, event: unknown) => request<{ matches: boolean; payload: unknown }>(`/ai/automations/${encoded(id)}/preview`, mutation('POST', { event })),
  runs: (limit = 50, signal?: AbortSignal) => request<AutomationRun[]>(`/ai/runs?limit=${limit}`, { signal }),
  purgeRuns: (before_days = 30) => request<CleanupResult>(`/ai/runs?before_days=${before_days}`, mutation('DELETE')),
  maintenance: (kind: 'history' | 'failed_jobs', before_days = 30) => request<CleanupResult>('/ai/maintenance', mutation('POST', { kind, before_days })),
}
