#!/usr/bin/env bash
# Smoke test for the Qwen3-Next-80B-A3B Ollama swap on Spark 2.
#
# Verifies:
#   1. Ollama serves the new model at /v1/chat/completions
#   2. The api container has the new LOCAL_OLLAMA_MODEL_NAME env var
#   3. /ask and translation E2E flows route via the new model and respond
#
# Run after `docker compose up -d --force-recreate api celery-worker celery-beat`
# following the model pull.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
# shellcheck disable=SC1091
set -a; source .env; set +a

EXPECTED_MODEL="${LOCAL_OLLAMA_MODEL_NAME:-qwen3-next:80b-a3b-instruct-q4_K_M}"

echo "==========================================="
echo "Spark 2 Qwen swap smoke test"
echo "expected model: $EXPECTED_MODEL"
echo "==========================================="

echo ""
echo "[1/5] ollama list shows the model"
ollama list | grep -q "${EXPECTED_MODEL%:*}" \
  || { echo "  FAIL: ${EXPECTED_MODEL%:*} not in ollama list"; exit 1; }
echo "  OK"

echo ""
echo "[2/5] direct Ollama call returns content"
RESP=$(curl -fsS -m 60 http://localhost:11434/v1/chat/completions \
  -H "Authorization: Bearer ollama" -H "Content-Type: application/json" \
  -d "$(jq -nc --arg m "$EXPECTED_MODEL" '{
    model: $m,
    messages: [
      {"role":"system","content":"Reply in 1 short sentence."},
      {"role":"user","content":"What is the OSHA threshold height for fall protection in construction?"}
    ],
    max_tokens: 80
  }')")
ANSWER=$(echo "$RESP" | jq -r '.choices[0].message.content // .choices[0].message.reasoning_content // ""')
if [ -z "$ANSWER" ]; then
  echo "  FAIL: empty response"
  echo "$RESP" | head -c 500
  exit 1
fi
echo "  OK: $(echo "$ANSWER" | head -c 200)"

echo ""
echo "[3/5] api container env shows new model"
ACTUAL=$(sg docker -c "docker exec constructai-api printenv LOCAL_OLLAMA_MODEL_NAME 2>/dev/null" || echo "")
if [ "$ACTUAL" != "$EXPECTED_MODEL" ]; then
  echo "  FAIL: container env LOCAL_OLLAMA_MODEL_NAME='$ACTUAL', expected '$EXPECTED_MODEL'"
  echo "  Run: docker compose -f infra/docker-compose.yml -f infra/docker-compose.demo.yml up -d --force-recreate api celery-worker celery-beat"
  exit 1
fi
echo "  OK"

echo ""
echo "[4/5] /ask routes through gateway -> qwen for intent classification"
curl -fsS -c /tmp/c1.txt -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo.pm@demo_session_01.test","password":"demo-password-demo_session_01"}' >/dev/null
PROJECT_ID=$(curl -fsSL -m 5 -b /tmp/c1.txt http://localhost:8000/api/v1/projects/ \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['id'])")
CSRF=$(grep csrf_token /tmp/c1.txt | awk '{print $7}')

ASK_RESP=$(curl -fsSL -m 90 -b /tmp/c1.txt -H "X-CSRF-Token: $CSRF" -X POST \
  "http://localhost:8000/api/v1/projects/$PROJECT_ID/ask" \
  -H "Content-Type: application/json" \
  -d '{"question":"What does UFGS 03 30 00 say about cure time?"}')
CONFIDENCE=$(echo "$ASK_RESP" | jq -r '.confidence // 0')
DSOURCES=$(echo "$ASK_RESP" | jq -r '.data_sources | length')
echo "  /ask confidence=$CONFIDENCE data_sources=$DSOURCES"
if [ "$DSOURCES" -lt 1 ]; then
  echo "  WARN: no corpus citations returned (check embedder + corpus)"
fi

echo ""
echo "[5/5] translation routes -> qwen for summarization task_class"
RFI_ID=$(sg docker -c "docker exec constructai-postgres psql -U constructai -t -c \"
  SELECT id FROM rfis WHERE project_id='$PROJECT_ID' AND status='answered' LIMIT 1
\"" | tr -d ' \n')
TR_RESP=$(curl -fsSL -m 60 -b /tmp/c1.txt -H "X-CSRF-Token: $CSRF" -X POST \
  "http://localhost:8000/api/v1/projects/$PROJECT_ID/rfis/$RFI_ID/translate?target_language=es" \
  -H "Content-Type: application/json" -d '{}')
echo "  translation response (first 250 chars):"
echo "$TR_RESP" | jq -r '.translated_question // .detail // ""' | head -c 250
echo ""

echo ""
echo "==========================================="
echo "Smoke test PASS — qwen swap operational"
echo "==========================================="
