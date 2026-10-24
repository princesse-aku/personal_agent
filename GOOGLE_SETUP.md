# Configuration Google (Calendar + Drive)

L'agent accède au Google Calendar et au Google Drive d'un **compte de test** via OAuth 2.0.
Le consentement est donné une seule fois (OAuth Playground) ; le serveur ne garde qu'un
refresh token dans ses variables d'environnement. Aucun fichier `credentials.json` ni `token.json`.

Scopes demandés (permissions minimales) :
- `https://www.googleapis.com/auth/calendar.events` — lire et gérer les événements ;
- `https://www.googleapis.com/auth/drive.readonly` — lire les fichiers Drive, sans pouvoir les modifier ;
- `https://www.googleapis.com/auth/gmail.send` — envoyer des emails (Railway bloque le SMTP, donc l'API Gmail en HTTPS est utilisée).

## 1. Projet Google Cloud (connecté avec le compte de test)

1. https://console.cloud.google.com/ → sélecteur de projet → **Nouveau projet** → `personal-agent`.
2. **API et services → Bibliothèque** : activer **Google Calendar API**, **Google Drive API** et **Gmail API**.
3. **Google Auth Platform** (écran de consentement OAuth) → **Commencer** :
   nom de l'application, e-mail d'assistance, audience **Externe**, e-mail de contact → **Créer**.
4. **Audience** → **Publier l'application** (passer « En production »).
   En mode « Test », Google fait expirer le refresh token au bout de 7 jours.
5. **Clients → Créer un client** → type **Application Web** → URI de redirection autorisé :
   `https://developers.google.com/oauthplayground` → **Créer**. Noter l'ID client et le code secret.

## 2. Refresh token (OAuth Playground)

1. https://developers.google.com/oauthplayground → ⚙️ → cocher **Use your own OAuth credentials** →
   coller l'ID client et le code secret.
2. Étape 1, champ « Input your own scopes » :
   `https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/gmail.send`
   → **Authorize APIs** → choisir le compte de test.
   Écran « Google n'a pas validé cette application » → **Paramètres avancés** → **Accéder à personal-agent** →
   cocher les deux autorisations → **Continuer**.
3. Étape 2 → **Exchange authorization code for tokens** → copier le **Refresh token** (commence par `1//`).

## 3. Variables d'environnement (Railway)

```
GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-...
GOOGLE_REFRESH_TOKEN=1//...
# optionnel : limiter Drive à un dossier (id visible dans l'URL du dossier)
# GOOGLE_DRIVE_FOLDER_ID=...
```

## 4. Cloisonnement (recommandé avec un compte personnel)

- **Agenda dédié** : Google Calendar → « Autres agendas » → **+** → **Créer un agenda** → `Agent IA`.
  Paramètres de cet agenda → section « Intégrer l'agenda » → copier l'**ID de l'agenda**
  (`…@group.calendar.google.com`) → variable `GOOGLE_CALENDAR_ID`.
- **Dossier Drive dédié** : créer un dossier `Agent IA` et y placer les documents de test.
  Ouvrir le dossier : l'ID est la fin de l'URL après `/folders/` → variable `GOOGLE_DRIVE_FOLDER_ID`.

L'agent n'utilise alors que cet agenda et ce dossier, jamais le reste du compte.

## 5. Données de démonstration

Dans l'agenda et le dossier dédiés : quelques événements dans Google Calendar (demain, la semaine prochaine)
et un ou deux Google Docs dans Drive (ex. « Proposition de projet — Agent IA »).

## Dépannage

- `L'autorisation Google a expiré ou a été révoquée` : refaire l'étape 2 et remplacer `GOOGLE_REFRESH_TOKEN`.
- `Accès refusé par Google` : vérifier que les deux API sont activées et que les deux scopes ont été cochés.
