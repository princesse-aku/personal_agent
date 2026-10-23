import { useEffect, useRef, useState } from 'react'
import './App.css'
import {
  addNote,
  checkPassword,
  createConversation,
  getMessages,
  listConversations,
  listModels,
  setPassword,
  streamChat,
  UnauthorizedError,
  type ConversationSummary,
  type TokenUsage,
  type ToolEvent,
} from './api'
import ChatWindow, { type ChatMessage } from './components/ChatWindow'
import LoginScreen from './components/LoginScreen'
import Sidebar from './components/Sidebar'

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : 'Une erreur est survenue.'
}

export default function App() {
  // null = checking, false = password needed, true = authorized
  const [authed, setAuthed] = useState<boolean | null>(null)
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [models, setModels] = useState<string[]>([])
  const [selectedModel, setSelectedModel] = useState('')
  const [noteMode, setNoteMode] = useState(false)
  const [retryPayload, setRetryPayload] = useState<{ text: string; model: string } | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    checkPassword()
      .then(() => setAuthed(true))
      .catch((err) => {
        if (err instanceof UnauthorizedError) setAuthed(false)
        else {
          setAuthed(true) // server reachable issues are shown in the normal UI
          setError(errorMessage(err))
        }
      })
  }, [])

  function handleAuthError(err: unknown): boolean {
    if (err instanceof UnauthorizedError) {
      setPassword('')
      setAuthed(false)
      return true
    }
    return false
  }

  useEffect(() => {
    if (!authed) return
    listModels()
      .then(({ models: list, default: def }) => {
        setModels(list)
        setSelectedModel(def)
      })
      .catch((err) => handleAuthError(err) || setError(errorMessage(err)))

    listConversations()
      .then((list) => {
        setConversations(list)
        if (list.length > 0) setActiveId(list[0].id)
      })
      .catch((err) => handleAuthError(err) || setError(errorMessage(err)))
  }, [authed])

  useEffect(() => {
    if (activeId === null) return
    let cancelled = false
    getMessages(activeId)
      .then((list) => {
        if (!cancelled) {
          setMessages(list.map(({ role, content }) => ({ role, content })))
        }
      })
      .catch((err) => !cancelled && setError(errorMessage(err)))
    return () => {
      cancelled = true
    }
  }, [activeId])

  function selectConversation(id: number) {
    if (loading || id === activeId) return
    setError(null)
    setRetryPayload(null)
    setDraft('')
    setMessages([])
    setActiveId(id)
  }

  async function handleNew() {
    if (loading) return
    setError(null)
    setRetryPayload(null)
    try {
      const id = await createConversation()
      setConversations((list) => [
        { id, created_at: new Date().toISOString(), preview: null },
        ...list,
      ])
      setDraft('')
      setMessages([])
      setActiveId(id)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  function handleStop() {
    abortRef.current?.abort()
  }

  async function sendChat(text: string, model: string) {
    if (activeId === null) return
    const isFirstMessage = messages.length === 0
    setError(null)
    setRetryPayload(null)
    setDraft('')
    setMessages((list) => [...list, { role: 'user', content: text }, { role: 'assistant', content: '' }])
    setLoading(true)

    const controller = new AbortController()
    abortRef.current = controller
    let usage: TokenUsage | undefined
    let notification: string | undefined
    let gotDelta = false

    try {
      await streamChat(activeId, text, model, {
        signal: controller.signal,
        onDelta: (chunk) => {
          gotDelta = true
          setMessages((list) => {
            const next = [...list]
            const last = next[next.length - 1]
            if (last?.role === 'assistant') {
              next[next.length - 1] = { ...last, content: last.content + chunk }
            }
            return next
          })
        },
        onTool: (event: ToolEvent) => {
          setMessages((list) => {
            const next = [...list]
            const last = next[next.length - 1]
            if (last?.role === 'assistant') {
              const others = (last.tools ?? []).filter((t) => t.id !== event.id)
              const existing = (last.tools ?? []).find((t) => t.id === event.id)
              const tools = existing
                ? (last.tools ?? []).map((t) => (t.id === event.id ? event : t))
                : [...others, event]
              next[next.length - 1] = { ...last, tools }
            }
            return next
          })
        },
        onMeta: (meta) => {
          if (meta.usage) usage = meta.usage
          if (meta.notification) notification = meta.notification
        },
      })

      setMessages((list) => {
        const next = [...list]
        const last = next[next.length - 1]
        if (last?.role === 'assistant') {
          next[next.length - 1] = { ...last, usage }
        }
        if (notification) {
          next.push({ role: 'system-notification', content: notification })
        }
        return next
      })
      if (isFirstMessage) setConversations(await listConversations())
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        // Stop: drop the optimistic user + empty/partial assistant; nothing was saved.
        setMessages((list) => {
          const withoutAssistant = list[list.length - 1]?.role === 'assistant' ? list.slice(0, -1) : list
          return withoutAssistant[withoutAssistant.length - 1]?.role === 'user'
            ? withoutAssistant.slice(0, -1)
            : withoutAssistant
        })
        setDraft(text)
        setError('Génération arrêtée. Aucun message de ce tour n’a été enregistré.')
      } else {
        if (handleAuthError(err)) return
        setMessages((list) => {
          let next = list
          if (next[next.length - 1]?.role === 'assistant') next = next.slice(0, -1)
          if (next[next.length - 1]?.role === 'user') next = next.slice(0, -1)
          return next
        })
        setDraft(text)
        setRetryPayload({ text, model })
        setError(errorMessage(err) + (gotDelta ? ' (réponse partielle non enregistrée)' : ''))
      }
    } finally {
      abortRef.current = null
      setLoading(false)
    }
  }

  async function handleSend() {
    if (activeId === null) return
    const text = draft.trim()
    if (!text) return

    if (noteMode) {
      setError(null)
      setRetryPayload(null)
      setDraft('')
      try {
        const note = await addNote(activeId, text)
        setMessages((list) => [...list, { role: note.role, content: note.content }])
      } catch (err) {
        setDraft(text)
        setError(errorMessage(err))
      }
      return
    }

    if (!selectedModel) {
      setError('Aucun modèle sélectionné.')
      return
    }
    await sendChat(text, selectedModel)
  }

  function handleRetry() {
    if (!retryPayload) return
    void sendChat(retryPayload.text, retryPayload.model)
  }

  if (authed === null) {
    return <div className="login-screen muted">Connexion…</div>
  }
  if (!authed) {
    return <LoginScreen onSuccess={() => setAuthed(true)} />
  }

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        onSelect={selectConversation}
        onNew={handleNew}
      />
      <main className="main">
        {error && (
          <div className="error" role="alert">
            {error}
            <button onClick={() => setError(null)} aria-label="Fermer">
              ×
            </button>
          </div>
        )}
        {activeId === null ? (
          <div className="empty">
            <p>Aucune conversation ouverte.</p>
            <button className="new-button" onClick={handleNew}>
              + Nouvelle conversation
            </button>
          </div>
        ) : (
          <ChatWindow
            messages={messages}
            loading={loading}
            draft={draft}
            onDraftChange={setDraft}
            onSend={() => void handleSend()}
            onStop={handleStop}
            onRetry={retryPayload ? handleRetry : null}
            noteMode={noteMode}
            onNoteModeChange={setNoteMode}
            models={models}
            selectedModel={selectedModel}
            onModelChange={setSelectedModel}
          />
        )}
      </main>
    </div>
  )
}
