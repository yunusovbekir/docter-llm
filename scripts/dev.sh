#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

for command in uv pnpm setsid; do
  if ! command -v "$command" >/dev/null; then
    printf 'Missing required command: %s\n' "$command" >&2
    exit 1
  fi
done

pnpm --dir frontend install --frozen-lockfile

pids=()
cleanup() {
  for pid in "${pids[@]}"; do
    kill -TERM -- "-$pid" 2>/dev/null || true
  done
  for pid in "${pids[@]}"; do
    wait "$pid" || true
  done
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Give each service a process group so shutdown also reaches its children.
setsid uv run docter &
pids+=("$!")
setsid pnpm --dir frontend dev --strictPort &
pids+=("$!")

wait -n "${pids[@]}"
