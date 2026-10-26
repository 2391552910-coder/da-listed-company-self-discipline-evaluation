#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 2 ]]; then echo 'Usage: native.sh api|worker|migrate|doctor PRIVATE_ENV_FILE [doctor arguments]' >&2; exit 2; fi
role="$1"
env_file="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
[[ -f "$env_file" ]] || { echo 'Environment file not found' >&2; exit 2; }
export DALUN_ENV_FILE="$env_file"
shift 2
cd "$(dirname "$0")/../backend"
case "$role" in
  api) exec "${DALUN_PYTHON:-python}" -m uvicorn app.main:app --host 127.0.0.1 --port "${DALUN_API_PORT:-8000}" ;;
  worker) exec "${DALUN_PYTHON:-python}" -m app.documents.cli worker ;;
  migrate) exec "${DALUN_PYTHON:-python}" -m alembic -c alembic.ini upgrade head ;;
  doctor) exec "${DALUN_PYTHON:-python}" -m app.operations.preflight "$@" ;;
  *) echo 'Use api, worker, migrate or doctor' >&2; exit 2 ;;
esac
