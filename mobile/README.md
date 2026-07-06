# NewsRead Mobile

React Native (Expo) app for iOS & Android. NewsRead is self-hosted, so the app
asks for your server's address on first launch, verifies it via `/api/health`,
then signs you in against that server.

## Development

```bash
npm install
npx expo start   # scan the QR with Expo Go, or press i / a for a simulator
```

For local dev, point the app at your machine's LAN IP (e.g.
`http://192.168.1.20:8000`) — `localhost` on a phone is the phone itself.

```bash
npm run typecheck   # tsc --noEmit
npm test            # jest (lib unit tests)
```

## Layout

- `src/app/` — expo-router screens: onboarding (server URL), login/register,
  article list, reader. Route access is gated by auth state via
  `Stack.Protected` in `_layout.tsx`.
- `src/lib/` — server URL normalization + health probe, fetch wrapper
  (bearer token, `X-Next-Cursor` keyset pagination), auth context
  (token in SecureStore), Expo push registration, reader HTML builder.

## Push notifications

Remote push requires a development build with an EAS project id
(`extra.eas.projectId` in app.json); Expo Go can't receive remote
notifications, so registration silently no-ops there.
