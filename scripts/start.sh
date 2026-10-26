#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 1 ]]; then echo 'Usage: start.sh PRIVATE_ENV_FILE [server] [no-build]' >&2; exit 2; fi
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
env_file="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
[[ -f "$env_file" ]] || { echo 'Environment file not found' >&2; exit 2; }
compose_args=(compose --env-file "$env_file" -f "$repo_root/compose.yml")
if [[ "${2:-}" == server ]]; then compose_args+=(-f "$repo_root/compose.server.yml"); fi
docker "${compose_args[@]}" config --quiet
up_args=(up -d)
if [[ "${2:-}" != no-build && "${3:-}" != no-build ]]; then up_args+=(--build); fi
docker "${compose_args[@]}" "${up_args[@]}"
echo 'Services started. Check /health/ready and /health/workers before creating work.'
