#!/usr/bin/env bash
# One-command README demo video: boots an isolated backend+frontend against
# the newsread_test DB, seeds demo content, records the scripted tour, and
# converts it to a GitHub-ready mp4.
#
#   run_demo.sh [output.mp4]
#
# Env knobs:
#   DEMO_SCHEME=dark|light   color scheme of the recording (default dark)
#   DEMO_SPEED=1.15          playback speed-up applied in the mp4 (default 1.0)
#   CHROME_PATH=...          alternative Chrome/Chromium binary
set -euo pipefail

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPTS/../../../.." && pwd)"
OUT_MP4="${1:-$REPO_ROOT/docs/assets/newsread-demo.mp4}"
BACKEND_PORT=8010
FRONTEND_PORT=3010
WORK="$(mktemp -d /tmp/newsread-demo.XXXXXX)"

echo "[demo] work dir: $WORK"
mkdir -p "$(dirname "$OUT_MP4")"

# The demo NEVER touches the real newsread DB — test DB only.
export NEWSREAD_DATABASE_URL="postgresql+asyncpg://newsread:newsread@localhost:5433/newsread_test"
export NEWSREAD_CORS_ORIGINS="http://localhost:${FRONTEND_PORT}"
# Related-articles requires embeddings to look "configured"; the key is never
# actually called because every seeded article already has its summary+vector.
export NEWSREAD_OPENAI_API_KEY="${NEWSREAD_OPENAI_API_KEY:-sk-demo-not-real}"
export NEWSREAD_OPENAI_EMBEDDING_MODEL="${NEWSREAD_OPENAI_EMBEDDING_MODEL:-text-embedding-3-small}"

# Match LISTEN sockets only — stale client connections from other tools also
# show up under these ports and must be neither a blocker nor a kill target.
for port in $BACKEND_PORT $FRONTEND_PORT; do
  if lsof -ti "tcp:$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[demo] port $port is already in use — stop that server first" >&2
    exit 1
  fi
done

PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  # Next dev spawns children; sweep listeners on the ports too.
  lsof -ti "tcp:$BACKEND_PORT" -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null || true
  lsof -ti "tcp:$FRONTEND_PORT" -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null || true
}
trap cleanup EXIT

echo "[demo] starting backend on :$BACKEND_PORT"
(cd "$REPO_ROOT/backend" && exec .venv/bin/python -m uvicorn app.main:app \
  --port "$BACKEND_PORT") >"$WORK/backend.log" 2>&1 &
PIDS+=($!)

for i in $(seq 1 60); do
  curl -sf "http://localhost:$BACKEND_PORT/api/health" >/dev/null && break
  [ "$i" = 60 ] && { echo "[demo] backend never became healthy — see $WORK/backend.log" >&2; exit 1; }
  sleep 1
done
echo "[demo] backend healthy"

echo "[demo] seeding demo data"
(cd "$REPO_ROOT/backend" && .venv/bin/python "$SCRIPTS/seed_demo_data.py" \
  --manifest "$WORK/manifest.json")

echo "[demo] starting frontend on :$FRONTEND_PORT"
(cd "$REPO_ROOT/frontend" && exec env NEXT_PUBLIC_API_URL="http://localhost:$BACKEND_PORT" \
  npm run dev -- -p "$FRONTEND_PORT") >"$WORK/frontend.log" 2>&1 &
PIDS+=($!)

for i in $(seq 1 120); do
  curl -sf "http://localhost:$FRONTEND_PORT" >/dev/null && break
  [ "$i" = 120 ] && { echo "[demo] frontend never came up — see $WORK/frontend.log" >&2; exit 1; }
  sleep 1
done
echo "[demo] frontend up"

if [ ! -d "$SCRIPTS/node_modules" ]; then
  echo "[demo] installing recorder deps"
  npm install --prefix "$SCRIPTS" >/dev/null
fi

echo "[demo] recording tour (${DEMO_SCHEME:-dark})"
node "$SCRIPTS/record_tour.js" \
  --base-url "http://localhost:$FRONTEND_PORT" \
  --api-url "http://localhost:$BACKEND_PORT" \
  --manifest "$WORK/manifest.json" \
  --out-dir "$WORK" \
  --scheme "${DEMO_SCHEME:-dark}" | tee "$WORK/recorder.log"

WEBM="$(sed -n 's/^VIDEO: //p' "$WORK/recorder.log" | tail -1)"
[ -f "$WEBM" ] || { echo "[demo] no video produced" >&2; exit 1; }

"$SCRIPTS/convert_to_mp4.sh" "$WEBM" "$OUT_MP4" "${DEMO_SPEED:-1.0}"
echo "[demo] done: $OUT_MP4"
echo "[demo] logs and intermediates in $WORK"
