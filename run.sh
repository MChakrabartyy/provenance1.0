#!/usr/bin/env bash
# One command to run everything locally. Usage: ./run.sh [setup|pipeline|test|evals|serve|all]
set -euo pipefail
cd "$(dirname "$0")/.."
PY=".venv/bin/python"
cmd="${1:-all}"
setup()    { [ -x "$PY" ] || uv venv --python 3.12 .venv; uv pip install -q --python "$PY" -e ./provenance 2>/dev/null || uv pip install -q --python "$PY" fastapi "uvicorn[standard]" pydantic openpyxl pyyaml pytest anthropic httpx; }
generate() { "$PY" -m provenance.data.synthetic.generator >/dev/null && echo "synthetic data generated (planted problems in provenance/data/synthetic/generated/planted.json)"; }
pipeline() { "$PY" -m provenance.ingestion.pipeline 2>provenance/infra/last_run.log.jsonl; "$PY" -c "from provenance.db import connect; from provenance.config import DATA_DIR; from provenance.ingestion.documents import ingest_documents; print('documents:', ingest_documents(connect(), DATA_DIR/'memos'))"; }
test_()    { "$PY" -m pytest provenance -q; }
evals()    { "$PY" -m provenance.copilot.evals.run_evals; }
serve()    { echo "open http://127.0.0.1:8000  (sign in from the header dropdown)"; "$PY" -m uvicorn provenance.api.main:app --host 127.0.0.1 --port 8000 "${@:2}"; }
case "$cmd" in
  setup) setup ;; generate) generate ;; pipeline) pipeline ;; test) test_ ;; evals) evals ;; serve) serve "$@" ;;
  all) setup; generate; pipeline; test_; evals || true; echo; echo "Done. Start the app with: ./provenance/run.sh serve" ;;
  *) echo "usage: $0 [setup|generate|pipeline|test|evals|serve|all]"; exit 2 ;;
esac
