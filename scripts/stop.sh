#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 1 ]]; then echo 'Usage: stop.sh PRIVATE_ENV_FILE [server]' >&2; exit 2; fi
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
env_file="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
[[ -f "$env_file" ]] || { echo 'Environment file not found' >&2; exit 2; }
compose_args=(compose --env-file "$env_file" -f "$repo_root/compose.yml")
if [[ "${2:-}" == server ]]; then compose_args+=(-f "$repo_root/compose.server.yml"); fi
docker "${compose_args[@]}" stop
echo 'Containers stopped; database and asset volumes are retained.'
