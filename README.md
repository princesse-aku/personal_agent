# Agent IA personnel avec Tool Calling

Agent IA capable d'utiliser des outils externes (recherche web, météo, puis Google Calendar,
email, Google Drive et Notion) à partir d'instructions en langage naturel.
Le LLM choisit dynamiquement les outils à appeler ; le backend les exécute et streame la réponse.

**Stack :** FastAPI + **PydanticAI** (backend), React + Vite (frontend), RodiumAI (LLM, API compatible OpenAI).
Un seul déploiement : FastAPI sert l'API sous `/api` et l'interface compilée sous `/`.

## Structure

```
backend/
  main.py            API FastAPI (/api/...) + service du frontend compilé
  agent/core.py      agent PydanticAI, registre des outils, date/heure/fuseau injectés
  agent/deps.py      dépendances passées à chaque outil
  prompts/agent.md   prompt système (choix des outils, confirmation, sources)
  tools/             un fichier par service externe
  database/, alembic/  SQLite + migrations
frontend/
  src/               interface de chat (pastilles d'outils en direct, écran de mot de passe)
Dockerfile           build du frontend puis image Python unique
railway.toml         déploiement Railway
```

## Outils

| Outil | Service | Statut |
|---|---|---|
| `web_search` | Tavily (web + actualités, avec sources) | ✅ |
| `get_weather` | WeatherAPI (actuel + prévisions 3 jours) | ✅ |
| Google Calendar | Google Calendar API (OAuth 2.0) | à venir |
| Email | SMTP | à venir |
| Google Drive | Google Drive API (OAuth 2.0) | à venir |
| Notion | Notion API | à venir |

## Lancement local

Backend :

```bash
cd backend
uv sync
cp .env.example .env      # remplir les clés
uv run alembic upgrade head
uv run fastapi dev main.py   # http://localhost:8000
```

Frontend (dans un second terminal) :

```bash
cd frontend
npm install
npm run dev                  # http://localhost:5173, proxy /api → :8000
```

## Déploiement (Railway)

1. Nouveau projet Railway → *Deploy from GitHub repo* → ce dépôt (le `Dockerfile` racine est détecté).
2. Variables : celles de `backend/.env.example` (`CORS_ORIGINS` n'est pas nécessaire en production).
3. Générer un domaine public : c'est l'URL unique à soumettre.

## Sécurité

- Aucune clé dans le code ; `.env` est ignoré par Git (voir `.gitignore`).
- `APP_PASSWORD` protège toute l'API (sauf `/api/health`) via l'en-tête `X-App-Password`.
  Le mot de passe est saisi dans l'interface et gardé seulement pour la durée de l'onglet.
- Les actions qui modifient des données (email, événements, Notion) exigent une confirmation explicite.

## Flux SSE de `POST /api/chat`

- `event: tool` → `{id, name, status: "running" | "done"}` à chaque appel d'outil ;
- `data: {"choices":[{"delta":{"content":...}}]}` → texte de la réponse ;
- `event: meta` → `usage` (tokens) ; `data: [DONE]` ; `event: error` en cas d'échec (rien n'est enregistré).
