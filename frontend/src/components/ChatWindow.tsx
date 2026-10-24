import { useEffect, useRef, type FormEvent, type KeyboardEvent } from 'react'
import Markdown from 'react-markdown'
import type { Role, TokenUsage, ToolEvent } from '../api'

export interface ChatMessage {
  role: Role
  content: string
  usage?: TokenUsage
  tools?: ToolEvent[]
}

// Human-readable labels for the backend tool names (unknown tools fall back to their raw name).
const TOOL_LABELS: Record<string, string> = {
  web_search: '🔍 Recherche web',
  get_weather: '🌤️ Météo',
  calendar_list_events: '📅 Lecture de l’agenda',
  calendar_create_event: '📅 Création d’événement',
  calendar_propose_update: '📅 Proposition de modification',
  calendar_propose_delete: '📅 Proposition de suppression',
  drive_search: '📁 Recherche Drive',
  drive_read_file: '📄 Lecture de document',
  confirm_action: '✅ Exécution confirmée',
  cancel_action: '↩️ Action annulée',
}

function ToolChips({ tools }: { tools: ToolEvent[] }) {
  return (
    <div className="tool-chips">
      {tools.map((t) => (
        <span key={t.id} className={`tool-chip ${t.status}`}>
          {TOOL_LABELS[t.name] ?? `🛠️ ${t.name}`}
          {t.status === 'running' ? '…' : ' ✓'}
        </span>
      ))}
    </div>
  )
}

interface ChatWindowProps {
  messages: ChatMessage[]
  loading: boolean
  draft: string
  onDraftChange: (value: string) => void
  onSend: () => void
  onStop: () => void
  onRetry: (() => void) | null
  noteMode: boolean
  onNoteModeChange: (value: boolean) => void
  models: string[]
  selectedModel: string
  onModelChange: (model: string) => void
}

function formatUsage(usage: TokenUsage): string {
  const total = usage.total_tokens
  if (typeof total === 'number') return `${total} tokens`
  const parts: string[] = []
  if (typeof usage.prompt_tokens === 'number') parts.push(`${usage.prompt_tokens} prompt`)
  if (typeof usage.completion_tokens === 'number') {
    parts.push(`${usage.completion_tokens} completion`)
  }
  return parts.length ? parts.join(' · ') : 'tokens'
}

export default function ChatWindow({
  messages,
  loading,
  draft,
  onDraftChange,
  onSend,
  onStop,
  onRetry,
  noteMode,
  onNoteModeChange,
  models,
  selectedModel,
  onModelChange,
}: ChatWindowProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!loading && draft.trim()) onSend()
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) handleSubmit(event)
  }

  return (
    <section className="chat">
      <div className="chat-toolbar">
        <label className="model-picker">
          <span>Modèle</span>
          <select
            value={selectedModel}
            onChange={(e) => onModelChange(e.target.value)}
            disabled={loading || models.length === 0}
          >
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="messages">
        {messages.length === 0 && !loading && (
          <p className="muted center">Demande-moi une recherche, la météo, ou une tâche à accomplir.</p>
        )}
        {messages.map((m, i) => {
          if (m.role === 'system-notification') {
            return (
              <div key={i} className="notification">
                {m.content}
              </div>
            )
          }
          if (m.role === 'note') {
            return (
              <div key={i} className="note">
                <span className="note-label">Note personnelle</span>
                <div className="note-body">{m.content}</div>
              </div>
            )
          }
          return (
            <div key={i} className={`bubble-wrap ${m.role}`}>
              {m.role === 'assistant' && m.tools && m.tools.length > 0 && <ToolChips tools={m.tools} />}
              <div className={`bubble ${m.role}`}>
                {m.role === 'assistant' ? (
                  m.content ? <Markdown
                      components={{
                        a: ({ href, children }) => (
                          <a href={href} target="_blank" rel="noopener noreferrer">
                            {children}
                          </a>
                        ),
                      }}
                    >
                      {m.content}
                    </Markdown> : <span className="typing">…</span>
                ) : (
                  m.content
                )}
              </div>
              {m.role === 'assistant' && m.usage && (
                <div className="token-usage">{formatUsage(m.usage)}</div>
              )}
            </div>
          )
        })}
        {loading && messages[messages.length - 1]?.role !== 'assistant' && (
          <div className="bubble assistant typing">…</div>
        )}
        <div ref={bottomRef} />
      </div>

      {onRetry && (
        <div className="retry-bar">
          <span>La dernière requête a échoué.</span>
          <button type="button" onClick={onRetry}>
            Réessayer
          </button>
        </div>
      )}

      <form className="composer" onSubmit={handleSubmit}>
        <div className="composer-tools">
          <label className="note-toggle">
            <input
              type="checkbox"
              checked={noteMode}
              onChange={(e) => onNoteModeChange(e.target.checked)}
              disabled={loading}
            />
            Mode note
          </label>
          {noteMode && (
            <span className="muted note-hint">Enregistrée dans le chat, jamais envoyée au LLM.</span>
          )}
        </div>
        <div className="composer-row">
          <textarea
            value={draft}
            onChange={(e) => onDraftChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={noteMode ? 'Écris une note personnelle…' : 'Écris ton message…'}
            rows={2}
            disabled={loading && !noteMode}
            autoFocus
          />
          {loading && !noteMode ? (
            <button type="button" className="stop-button" onClick={onStop}>
              Stop
            </button>
          ) : (
            <button type="submit" disabled={loading || !draft.trim()}>
              {noteMode ? 'Ajouter' : 'Envoyer'}
            </button>
          )}
        </div>
      </form>
    </section>
  )
}
