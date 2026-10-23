import { useState, type FormEvent } from 'react'
import { checkPassword, setPassword, UnauthorizedError } from '../api'

export default function LoginScreen({ onSuccess }: { onSuccess: () => void }) {
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!value) return
    setBusy(true)
    setError(null)
    setPassword(value)
    try {
      await checkPassword()
      onSuccess()
    } catch (err) {
      setPassword('')
      setError(err instanceof UnauthorizedError ? 'Mot de passe incorrect.' : 'Impossible de joindre le serveur.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={handleSubmit}>
        <h1>Agent IA personnel</h1>
        <p className="muted">Saisis le mot de passe d’accès pour tester l’agent.</p>
        <input
          type="password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Mot de passe"
          autoFocus
        />
        {error && <p className="login-error">{error}</p>}
        <button type="submit" disabled={busy || !value}>
          {busy ? 'Vérification…' : 'Entrer'}
        </button>
      </form>
    </div>
  )
}
