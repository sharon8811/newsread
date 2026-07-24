# NewsRead History extension

Manifest V3 Chrome/Chromium extension for the NewsRead browser-history feature.
It captures one plain-text DOM representation, keeps a bounded IndexedDB
outbox, and syncs batches to the paired NewsRead server. It does not use an
offscreen document, local embedding model, or `SemanticEmbedder`.

## Development

```bash
npm install
npm test
npm run build
```

Load `extension/dist/` from `chrome://extensions` using **Load unpacked**.
The package is native browser ESM compiled by TypeScript; there is no bundler
and no remotely hosted code.

## Pairing and permissions

Create a one-time token in NewsRead under Settings → Browser history. Open the
extension, enter the NewsRead origin and token, and approve access to that exact
origin. The extension requests Chrome's `history` permission only if the user
starts the optional metadata import.

Automatic capture uses a declared content script on HTTP(S) pages. Incognito is
disabled by the manifest. The service worker receives visible text only, checks
pause/exclusion rules before queueing, and never logs page text or tokens.

## Manual unpacked-extension checklist

- Pair against a feature-enabled NewsRead server and confirm the one-time token
  is not shown again.
- Visit an HTTP(S) article and confirm the badge shows queued work, then clears
  after sync.
- Search for the captured title and visible text in NewsRead History.
- Pause capture, visit another page, and confirm it is not queued.
- Add a domain exclusion, visit that domain, and confirm it is not queued.
- Switch to metadata-only mode and confirm visible text is absent after sync.
- Disconnect or revoke the connection and confirm the popup shows the failure.
- Stop the backend, visit a page, restart it, and confirm the outbox retries.
- Test `429 Retry-After` handling and confirm the extension does not spin.
- Use Index current page from the popup.
- Grant optional Chrome history access, begin an import, and test cancellation.
- Confirm `chrome://`, extension pages, localhost/private hosts, NewsRead itself,
  and incognito browsing cannot enter the queue.
- Reload the service worker from `chrome://extensions` and confirm there are no
  unhandled errors.

## Privacy

What the extension captures, what is always excluded, permission-by-permission
explanations, retention, and deletion guarantees are documented in
[docs/browser-history-privacy.md](../docs/browser-history-privacy.md). Keep that
document in sync with any capture-behavior change.

## Packaging

Update `package.json`'s version, then run `npm test && npm run build`. The
build copies the NewsRead license and required Smart History Apache attribution
into `dist/` and packages its contents (not the `dist` directory itself) into
`newsread-history-extension.zip`, which the backend serves from
Settings → Browser history as the in-app "Download extension" button
(`NEWSREAD_EXTENSION_PACKAGE` overrides the path; docker-compose mounts
`./extension` for this). Zipping uses the system `zip` binary and is skipped
with a warning when it is unavailable.
