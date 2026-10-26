#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../backend"
case "${1:-api}" in
  migrate) exec "${DALUN_PYTHON:-python}" -m alembic -c alembic.ini upgrade head ;;
  worker) exec "${DALUN_PYTHON:-python}" -m app.documents.cli worker ;;
  api) exec "${DALUN_PYTHON:-python}" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 ;;
  *) echo "Use api, worker or migrate" >&2; exit 2 ;;
esac
