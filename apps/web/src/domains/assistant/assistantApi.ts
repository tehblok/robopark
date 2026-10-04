import { request } from '../../api'

export type AiStatus = {
  supported: boolean; installed: boolean; enabled: boolean; ready: boolean; reason: string | null
  model: string; backend: 'cuda' | null; can_manage: boolean
  counts: { documents: number; candidates: number; jobs: number }
  knowledge_bundle?: {
    state: 'pending' | 'importing' | 'ready' | 'failed' | 'paused' | 'unavailable'
    total: number; processed: number; created: number; skipped: number; error: string | null
  }
}
export type AiConfig = { enabled: boolean; learning_enabled: boolean; revision: number }
export type AiPrompt = { role: 'mechanic' | 'operator' | 'admin'; content: string; revision: number }
export type AiSource = { id: string; title: string; excerpt: string; trust: KnowledgeTrust }
export type AiMessage = { id: string; role: 'user' | 'assistant'; content: string; sources: AiSource[]; created_at: string }
export type AiJob = {
  id: string; kind: string; state: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  created_at: string; updated_at: string; error: string | null; result: unknown
}
export type Conversation = { id: string; title: string; park_id: number; issue_key: string | null; updated_at: string }
export type ConversationDetail = Conversation & { messages: AiMessage[]; jobs: AiJob[] }
export type KnowledgeKind = 'manual' | 'chat' | 'ticket' | 'note'
export type KnowledgeState = 'active' | 'candidate' | 'rejected' | 'deleted'
export type KnowledgeTrust = 'instruction' | 'experience' | 'unverified'
export type KnowledgeDocument = {
  id: string; title: string; kind: KnowledgeKind; state: KnowledgeState; trust: KnowledgeTrust
  park_id: number | null; source_ref: string; updated_at: string; revision: number; content?: string
}
export type KnowledgeWrite = { title: string; content: string; kind: KnowledgeKind; park_id?: number | null; state?: KnowledgeState }
export type KnowledgeImportDocument = { title: string; content: string; kind: KnowledgeKind; source_ref: string }
export type Connector = { id: string; name: string; url: string; method: 'GET' | 'POST' | 'PUT' | 'PATCH'; enabled: boolean; token_set: boolean; revision: number }
export type ConnectorWrite = { name: string; url: string; method: Connector['method']; token?: string; enabled?: boolean }
export type AssistantScript = { id: string; name: string; source: string; revision: number; enabled: boolean; tested_revision: number | null; updated_at: string }
export type AutomationFilters = { component_ids: string[]; defect_codes: string[]; keywords: string[] }
export type AutomationAction = { connector_id?: string; script_id?: string; body: unknown }
export type Automation = { id: string; name: string; park_id: number; enabled: boolean; revision: number; filters: AutomationFilters; action: AutomationAction; updated_at: string }
export type AutomationRun = { id: string; automation_id: string; event_key: string; state: string; error: string | null; created_at: string; updated_at: string; result: unknown }

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

  documents: (filters: { q?: string; park_id?: number; state?: KnowledgeState; offset?: number; limit?: number; signal?: AbortSignal } = {}) => {
    const query = new URLSearchParams()
    if (filters.q) query.set('q', filters.q)
    if (filters.park_id !== undefined) query.set('park_id', String(filters.park_id))
    if (filters.state) query.set('state', filters.state)
    query.set('offset', String(filters.offset ?? 0)); query.set('limit', String(filters.limit ?? 30))
    return request<{ items: KnowledgeDocument[]; total: number; offset: number; limit: number }>(`/ai/documents?${query}`, { signal: filters.signal })
  },
  document: (id: string, signal?: AbortSignal) => request<KnowledgeDocument>(`/ai/documents/${encoded(id)}`, { signal }),
  createDocument: (value: KnowledgeWrite) => request<KnowledgeDocument>('/ai/documents', mutation('POST', value)),
  updateDocument: (id: string, value: Partial<KnowledgeWrite> & { revision: number }) => request<KnowledgeDocument>(`/ai/documents/${encoded(id)}`, mutation('PATCH', value)),
  deleteDocument: (id: string) => request<void>(`/ai/documents/${encoded(id)}`, mutation('DELETE')),
  importDocuments: (documents: KnowledgeImportDocument[], park_id: number | null, activate_manuals: boolean, activate_unverified = false) =>
    request<{ created: number; duplicates: number; rejected: number }>('/ai/documents/import', mutation('POST', { documents, park_id, activate_manuals, activate_unverified })),

  conversations: (signal?: AbortSignal) => request<Conversation[]>('/ai/conversations', { signal }),
  createConversation: (value: { title?: string; park_id: number; issue_key?: string }) => request<Conversation>('/ai/conversations', mutation('POST', value)),
  conversation: (id: string, signal?: AbortSignal) => request<ConversationDetail>(`/ai/conversations/${encoded(id)}`, { signal }),
  deleteConversation: (id: string) => request<void>(`/ai/conversations/${encoded(id)}`, mutation('DELETE')),
  sendMessage: (conversationId: string, content: string, idempotency_key: string) => request<AiJob>(`/ai/conversations/${encoded(conversationId)}/messages`, mutation('POST', { content, idempotency_key })),
  job: (id: string, signal?: AbortSignal) => request<AiJob>(`/ai/jobs/${encoded(id)}`, { signal }),
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
  purgeRuns: (before_days = 30) => request<{ deleted: number }>(`/ai/runs?before_days=${before_days}`, mutation('DELETE')),
  maintenance: (kind: 'history' | 'failed_jobs', before_days = 30) => request<{ deleted: number }>('/ai/maintenance', mutation('POST', { kind, before_days })),
}

const MAX_IMPORT_BYTES = 64 * 1024 * 1024
const allowedKinds = new Set<KnowledgeKind>(['manual', 'chat', 'ticket', 'note'])

function asImportDocument(value: unknown): KnowledgeImportDocument | null {
  if (!value || typeof value !== 'object') return null
  const item = value as Record<string, unknown>
  if (typeof item.title !== 'string' || !item.title.trim() || typeof item.content !== 'string' || !item.content.trim()
    || typeof item.source_ref !== 'string' || !item.source_ref.trim() || typeof item.kind !== 'string'
    || !allowedKinds.has(item.kind as KnowledgeKind)) return null
  return { title: item.title.trim(), content: item.content.trim(), kind: item.kind as KnowledgeKind, source_ref: item.source_ref.trim() }
}

export async function parseKnowledgeFile(file: File): Promise<{ documents: KnowledgeImportDocument[]; rejected: number }> {
  if (file.size > MAX_IMPORT_BYTES) throw new Error('Файл больше 64 МиБ')
  const text = await file.text()
  let values: unknown[]
  if (file.name.toLocaleLowerCase().endsWith('.jsonl')) {
    values = text.split(/\r?\n/).filter(line => line.trim()).map(line => {
      try { return JSON.parse(line) as unknown } catch { return null }
    })
  } else {
    const parsed = JSON.parse(text) as unknown
    values = Array.isArray(parsed) ? parsed : [parsed]
  }
  const documents = values.map(asImportDocument).filter((item): item is KnowledgeImportDocument => item !== null)
  return { documents, rejected: values.length - documents.length }
}
