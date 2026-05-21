#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Wire the bundled demo warehouse so the Library tab populates
# without external paths. User-set env vars win first.
if [ -z "${PROTEOSPHERE_V2_INGEST_ROOT:-}" ] && [ -f "$HERE/demo_warehouse/catalog/v2.duckdb" ]; then
    export PROTEOSPHERE_V2_INGEST_ROOT="$HERE/demo_warehouse"
fi
if [ -z "${PROTEOSPHERE_V2_EMBEDDINGS:-}" ] && [ -d "$HERE/demo_warehouse/embeddings" ]; then
    export PROTEOSPHERE_V2_EMBEDDINGS="$HERE/demo_warehouse/embeddings"
fi

PORT="${1:-8765}"
# Best-effort: kill any prior listener so a second double-run doesn't
# end up with two bound processes (Windows AND Unix can both do this).
if command -v lsof >/dev/null 2>&1; then
    PIDS="$(lsof -ti tcp:$PORT 2>/dev/null || true)"
    if [ -n "$PIDS" ]; then
        echo "killing prior listener(s) on :$PORT -- $PIDS"
        kill -9 $PIDS 2>/dev/null || true
        sleep 1
    fi
fi
cd "$HERE"
exec python -X utf8 -m api.model_studio.server_v2 --port "$PORT"
