// Thin typed wrappers around the FastAPI backend (proxied under /api by Vite).

const API_BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') ?? '/api'

export type Role = 'user' | 'assistant' | 'system-notification' | 'note'

export interface ConversationSummary {
  id: number
  created_at: string
  preview: string | null
}

export interface Message {
  seq: number
  role: Role
  content: string
  created_at: string
}

export interface ModelsResponse {
  models: string[]
  default: string
}

export interface TokenUsage {
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
}

export interface StreamMeta {
  notification?: string
  usage?: TokenUsage
}

export interface ToolEvent {
  id: string
  name: string
  status: 'running' | 'done'
}

export interface StreamChatHandlers {
  onDelta: (text: string) => void
  onMeta?: (meta: StreamMeta) => void
  onTool?: (event: ToolEvent) => void
  signal?: AbortSignal
}

// Access password, kept only for the browser tab's lifetime (never in the bundle).
const PASSWORD_KEY = 'agent-app-password'

export function getPassword(): string {
  try {
    return sessionStorage.getItem(PASSWORD_KEY) ?? ''
  } catch {
    return ''
  }
}

export function setPassword(value: string): void {
  try {
    if (value) sessionStorage.setItem(PASSWORD_KEY, value)
    else sessionStorage.removeItem(PASSWORD_KEY)
  } catch {
    /* storage unavailable: the password just won't persist across reloads */
  }
}

export class UnauthorizedError extends Error {}

function authHeaders(): Record<string, string> {
  return { 'Content-Type': 'application/json', 'X-App-Password': getPassword() }
}

async function failure(response: Response): Promise<Error> {
  const body = await response.json().catch(() => null)
  const detail = typeof body?.detail === 'string' ? body.detail : null
  if (response.status === 401) return new UnauthorizedError(detail ?? 'Mot de passe requis.')
  return new Error(detail ?? `Erreur ${response.status}`)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { ...authHeaders(), ...init?.headers },
    })
  } catch {
    throw new Error('Impossible de joindre le serveur.')
  }
  if (!response.ok) throw await failure(response)
  return response.json() as Promise<T>
}

export function checkPassword(): Promise<{ ok: boolean }> {
  return request('/auth/check')
}

export function listModels(): Promise<ModelsResponse> {
  return request('/models')
}

export function listConversations(): Promise<ConversationSummary[]> {
  return request('/conversations')
}

export async function createConversation(): Promise<number> {
  const { conversation_id } = await request<{ conversation_id: number }>('/conversations', {
    method: 'POST',
  })
  return conversation_id
}

export function getMessages(conversationId: number): Promise<Message[]> {
  return request(`/conversations/${conversationId}/messages`)
}

export async function addNote(conversationId: number, content: string): Promise<Message> {
  return request(`/conversations/${conversationId}/notes`, {
    method: 'POST',
    body: JSON.stringify({ content }),
  })
}

export async function streamChat(
  conversationId: number,
  message: string,
  model: string,
  handlers: StreamChatHandlers,
): Promise<void> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}/chat`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ conversation_id: conversationId, message, model }),
      signal: handlers.signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw err
    }
    throw new Error('Impossible de joindre le serveur.')
  }

  if (!response.ok) throw await failure(response)
  if (!response.body) {
    throw new Error('Réponse stream vide.')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let sawDone = false
  let streamError: string | null = null

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE events are separated by a blank line.
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const rawEvent = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)

      let eventName = 'message'
      const dataLines: string[] = []
      for (const line of rawEvent.split('\n')) {
        if (line.startsWith('event:')) {
          eventName = line.slice(6).trim()
        } else if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trim())
        }
      }
      const data = dataLines.join('\n')
      if (!data) continue

      if (eventName === 'error') {
        try {
          const parsed = JSON.parse(data) as { detail?: string }
          streamError = parsed.detail ?? 'Erreur pendant le streaming.'
        } catch {
          streamError = 'Erreur pendant le streaming.'
        }
        continue
      }

      if (eventName === 'tool') {
        try {
          handlers.onTool?.(JSON.parse(data) as ToolEvent)
        } catch {
          /* ignore malformed tool event */
        }
        continue
      }

      if (eventName === 'meta') {
        try {
          handlers.onMeta?.(JSON.parse(data) as StreamMeta)
        } catch {
          /* ignore malformed meta */
        }
        continue
      }

      if (data === '[DONE]') {
        sawDone = true
        continue
      }

      try {
        const payload = JSON.parse(data) as {
          choices?: Array<{ delta?: { content?: string } }>
        }
        const content = payload.choices?.[0]?.delta?.content
        if (content) handlers.onDelta(content)
      } catch {
        /* ignore non-JSON keepalive lines */
      }
    }
  }

  if (streamError) {
    throw new Error(streamError)
  }
  if (!sawDone) {
    throw new Error('Le flux a été interrompu avant la fin.')
  }
}
