# Messaging integrations — provider setup (Slack + Microsoft Teams)

One-time setup to obtain the credentials that power "share to Slack/Teams
as the user". Everything lands in `.env` (see the messaging section in
`.env.example`). Nothing here is code — it's app registrations on the
provider side.

## 0. Prerequisites

### 0.1 Token encryption key

Per-user Slack/Teams tokens are encrypted at rest in Postgres with a Fernet
key. Generate one (no dependencies needed):

```bash
python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Put it in `.env` as `NEWSREAD_TOKEN_ENCRYPTION_KEY`. Losing/rotating the key
invalidates all stored connections (users just reconnect).

### 0.2 HTTPS tunnel for OAuth callbacks (dev only)

Slack requires **HTTPS** redirect URLs — `http://localhost` is rejected — so
local dev needs a public tunnel to the backend:

1. Sign up at [ngrok.com](https://ngrok.com) (free tier is enough).
2. Install: `brew install ngrok`, then `ngrok config add-authtoken <token>`
   (token is on the dashboard).
3. Claim your free **static domain**: dashboard → *Universal Gateway →
   Domains* → *New Domain*. You get something like
   `funky-name.ngrok-free.app`. A static domain matters because both Slack
   and Entra require exact-match registered redirect URLs — a random
   per-run URL would mean re-editing both apps every restart.
4. Run it whenever you're testing OAuth: `ngrok http --domain=<your-domain> 8000`
   (adjust `8000` if your backend runs elsewhere).

Set in `.env`:

```
NEWSREAD_OAUTH_REDIRECT_BASE=https://<your-domain>.ngrok-free.app
```

In production this is simply the backend's public HTTPS origin.

## 1. Slack app

Slack posts genuinely **as the user** via user-token scopes — their name and
avatar, no bot attribution.

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App**
   → **From a manifest** → pick your workspace.
2. Choose YAML and paste (replace the redirect domain first):

   ```yaml
   display_information:
     name: NewsRead
     description: Share articles from NewsRead to your channels
     background_color: "#1a1d21"
   oauth_config:
     redirect_urls:
       - https://<your-domain>.ngrok-free.app/api/integrations/slack/callback
     scopes:
       user:
         - chat:write      # post messages as the user
         - channels:read   # list public channels for the target picker
         - groups:read     # list private channels
         - im:read         # list DMs
         - mpim:read       # list group DMs
         - users:read      # resolve DM display names
         - team:read       # workspace name for the settings UI
   settings:
     org_deploy_enabled: false
     socket_mode_enabled: false
     token_rotation_enabled: false
   ```

   Note `token_rotation_enabled: false`: Slack user tokens then don't expire,
   which keeps v1 free of refresh-token plumbing.
3. Create the app, then on **Basic Information** copy **Client ID** and
   **Client Secret** into `.env`:

   ```
   NEWSREAD_SLACK_CLIENT_ID=...
   NEWSREAD_SLACK_CLIENT_SECRET=...
   ```

4. Do **not** click "Install to Workspace" — installation happens through the
   app's own OAuth flow (Settings → Connections → Connect Slack).

Basic Information also shows a **Signing Secret**, **App ID**, and
**Verification Token**. The signing secret verifies *inbound* requests from
Slack (Events API, slash commands, interactivity) — unused by the outbound
share flow, but copy it into `NEWSREAD_SLACK_SIGNING_SECRET` now anyway so a
future inbound feature (e.g. syncing channel replies back as comments)
doesn't require a dashboard trip. The App ID is a public identifier, not a
credential, and the verification token is deprecated — skip both.

If the redirect domain ever changes, update it under **OAuth & Permissions →
Redirect URLs**.

Scope of this app: it works for the workspace you created it in (and any
workspace, once you enable public distribution later — not needed for v1).

## 2. Microsoft Teams (Entra ID app registration)

Teams posting uses Microsoft Graph **delegated** permissions — also sends as
the signed-in user. Requires a work/school Microsoft account (personal Teams
is not supported by these Graph APIs).

1. Go to [portal.azure.com](https://portal.azure.com) → **Microsoft Entra ID**
   → **App registrations** → **New registration**.
   - Name: `NewsRead`
   - Supported account types: **Accounts in any organizational directory**
     (multitenant — works with any work/school account; pick single tenant
     instead if you want to pin it to your org, and then set
     `NEWSREAD_TEAMS_TENANT` to your Directory (tenant) ID).
   - Redirect URI: platform **Web**, value
     `https://<your-domain>.ngrok-free.app/api/integrations/teams/callback`
2. After creation, under **Authentication → Web → Redirect URIs**, also add
   `http://localhost:8000/api/integrations/teams/callback` (Entra allows
   localhost over http; handy if you ever test Teams without the tunnel).
3. **Overview** page: copy **Application (client) ID** → `.env`:

   ```
   NEWSREAD_TEAMS_CLIENT_ID=...
   ```

4. **Certificates & secrets** → **New client secret** → pick 24 months →
   copy the **Value** column immediately (it's shown only once):

   ```
   NEWSREAD_TEAMS_CLIENT_SECRET=...
   ```

5. **API permissions** → **Add a permission** → **Microsoft Graph** →
   **Delegated permissions**, add:

   | Permission | Why |
   |---|---|
   | `ChatMessage.Send` | send to 1:1 / group chats as the user |
   | `ChannelMessage.Send` | send to team channels as the user |
   | `Team.ReadBasic.All` | list the user's teams (target picker) |
   | `Channel.ReadBasic.All` | list channels in those teams |
   | `Chat.ReadBasic` | list the user's chats |
   | `User.Read` | basic profile (default, usually pre-added) |
   | `offline_access` | refresh tokens (Graph access tokens live ~1h) |

   None of these need admin consent by Graph's defaults, so the "Grant admin
   consent" button can stay untouched. Caveat: some tenants disable *user*
   consent for third-party apps entirely — if the connect flow errors with
   "Need admin approval", a tenant admin has to approve the app once.

`NEWSREAD_TEAMS_TENANT` stays `organizations` for the multitenant setup.

## 3. WhatsApp

Nothing to set up. There is no API for sending as a personal WhatsApp
account, so WhatsApp is implemented as a `wa.me` deep link: the composed
(optionally LLM-refined) message + article URL open in WhatsApp and the user
taps send. No credentials involved.

## 4. Final `.env` checklist

```
NEWSREAD_TOKEN_ENCRYPTION_KEY=   # step 0.1
NEWSREAD_OAUTH_REDIRECT_BASE=    # step 0.2 (https tunnel in dev)
NEWSREAD_SLACK_CLIENT_ID=        # step 1
NEWSREAD_SLACK_CLIENT_SECRET=    # step 1
NEWSREAD_SLACK_SIGNING_SECRET=   # step 1 (future inbound features only)
NEWSREAD_TEAMS_CLIENT_ID=        # step 2
NEWSREAD_TEAMS_CLIENT_SECRET=    # step 2
NEWSREAD_TEAMS_TENANT=organizations
NEWSREAD_FRONTEND_BASE_URL=http://localhost:3000
```

Restart the backend after editing `.env`. Platforms with missing credentials
simply show as unavailable in Settings → Connections; each one lights up
independently as its credentials appear.
