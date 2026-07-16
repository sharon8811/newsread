#!/usr/bin/env bash
# Convert the Playwright .webm recording into a GitHub-friendly .mp4:
# H.264 + yuv420p (plays everywhere), even dimensions, faststart for
# progressive playback, 30fps.
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "usage: convert_to_mp4.sh <input.webm> <output.mp4> [speed]" >&2
  echo "  speed: optional playback multiplier, e.g. 1.15 to tighten pacing" >&2
  exit 1
fi

IN="$1"
OUT="$2"
SPEED="${3:-1.0}"

VF="scale=trunc(iw/2)*2:trunc(ih/2)*2"
if [ "$SPEED" != "1.0" ] && [ "$SPEED" != "1" ]; then
  VF="setpts=PTS/${SPEED},${VF}"
fi

ffmpeg -y -i "$IN" \
  -vf "$VF" \
  -c:v libx264 -preset slow -crf 20 -pix_fmt yuv420p \
  -r 30 -an -movflags +faststart \
  "$OUT"

echo "wrote $OUT ($(du -h "$OUT" | cut -f1 | tr -d ' '))"
