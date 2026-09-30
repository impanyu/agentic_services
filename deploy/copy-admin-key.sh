#!/usr/bin/env bash
set -euo pipefail

if ! command -v pbcopy >/dev/null 2>&1; then
  echo "This helper requires macOS pbcopy." >&2
  exit 1
fi

gcloud compute ssh agentic-wiki \
  --zone us-central1-a \
  --project impanyu \
  --command="sed -n 's/^ADMIN_API_KEY=//p' /mnt/disks/agentic-services/app/.env.production" \
  2>/dev/null | tail -n 1 | tr -d '\r\n' | pbcopy

key_length="$(pbpaste | wc -c | tr -d ' ')"
if [[ "$key_length" -lt 20 ]]; then
  echo "Unable to copy a valid admin key." >&2
  exit 1
fi

echo "Admin key copied to the clipboard ($key_length characters)."
