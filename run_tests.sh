#!/bin/bash
API="http://localhost:8000"
COOKIES="/tmp/test_cookies.txt"
PASS=0; FAIL=0; ISSUES=""
PID="74dddd28-28bc-43cc-b810-b26b7c88b303"

login() {
  printf '{"email":"%s","password":"Demo2026!"}' "$1" | curl -s -X POST "$API/api/v1/auth/login" -H "Content-Type: application/json" -d @- -c "$COOKIES" > /dev/null
  CSRF=$(awk '/csrf_token/{print $NF}' "$COOKIES")
}

check() {
  local name="$1" expected="$2" actual="$3"
  if [ "$actual" = "$expected" ]; then
    PASS=$((PASS+1)); echo "  PASS: $name"
  else
    FAIL=$((FAIL+1)); ISSUES="$ISSUES|FAIL: $name (exp=$expected got=$actual)"
    echo "  FAIL: $name (exp=$expected got=$actual)"
  fi
}

echo "=== AUTH TESTS ==="
login "pm@buildright.dev"

# Non-existent email
CODE=$(printf '{"email":"nobody@example.com","password":"wrong"}' | curl -s -o /dev/null -w "%{http_code}" -X POST "$API/api/v1/auth/login" -H "Content-Type: application/json" -d @-)
check "Non-existent email -> 401" "401" "$CODE"

# Unauthenticated access
CODE=$(curl -s -o /dev/null -w "%{http_code}" "$API/api/v1/projects/")
check "Unauth /projects -> 403" "403" "$CODE"

# POST without CSRF
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/api/v1/projects/" -H "Content-Type: application/json" -b "$COOKIES" -d '{"name":"test"}')
check "POST no CSRF -> 403" "403" "$CODE"

# PM cant create projects
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/api/v1/projects/" -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF" -b "$COOKIES" -d '{"name":"Test","type":"commercial"}')
check "PM cant create project -> 403" "403" "$CODE"

# Login CSRF-exempt
CODE=$(printf '{"email":"pm@buildright.dev","password":"Demo2026!"}' | curl -s -o /dev/null -w "%{http_code}" -X POST "$API/api/v1/auth/login" -H "Content-Type: application/json" -d @-)
check "Login CSRF-exempt -> 200" "200" "$CODE"

echo ""
echo "=== API FORMAT TESTS ==="
PROJ_FMT=$(curl -s "$API/api/v1/projects/" -b "$COOKIES" | python3 -c "import sys,json; d=json.load(sys.stdin); print('data' if 'data' in d else 'other')" 2>/dev/null)
check "Projects -> {data:[]}" "data" "$PROJ_FMT"

RFI_FMT=$(curl -s "$API/api/v1/projects/$PID/rfis?" -b "$COOKIES" | python3 -c "import sys,json; d=json.load(sys.stdin); print('data' if 'data' in d else 'other')" 2>/dev/null)
check "RFIs -> {data:[]}" "data" "$RFI_FMT"

DOC_FMT=$(curl -s "$API/api/v1/documents/?project_id=$PID" -b "$COOKIES" | python3 -c "import sys,json; d=json.load(sys.stdin); print('data' if 'data' in d else 'other')" 2>/dev/null)
check "Docs -> {data:[]}" "data" "$DOC_FMT"

ERR_FMT=$(curl -s "$API/api/v1/projects/00000000-0000-0000-0000-000000000000" -b "$COOKIES" | python3 -c "import sys,json; d=json.load(sys.stdin); print('detail' if 'detail' in d else 'other')" 2>/dev/null)
check "404 -> {detail:...}" "detail" "$ERR_FMT"

echo ""
echo "=== CRUD & PERSISTENCE ==="
# Close RFI
RFI_ID=$(docker exec constructai-postgres psql -U constructai -d constructai -t -c "SELECT id FROM rfis WHERE status='open' LIMIT 1" 2>/dev/null | tr -d ' \n')
if [ -n "$RFI_ID" ]; then
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/api/v1/projects/$PID/rfis/$RFI_ID/close" -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF" -b "$COOKIES" -d '{"answer":"Closed via test"}')
  check "Close RFI -> 200" "200" "$CODE"
  STATUS=$(docker exec constructai-postgres psql -U constructai -d constructai -t -c "SELECT status FROM rfis WHERE id='$RFI_ID'" 2>/dev/null | tr -d ' \n')
  check "RFI status=closed in DB" "closed" "$STATUS"
else
  echo "  SKIP: No open RFI to close"
fi

# Submittal persistence
SUB_CT=$(docker exec constructai-postgres psql -U constructai -d constructai -t -c "SELECT count(*) FROM submittals" 2>/dev/null | tr -d ' \n')
check "Submittals in DB > 0" "1" "$([ "$SUB_CT" -gt 0 ] && echo 1 || echo 0)"

PLI_CT=$(docker exec constructai-postgres psql -U constructai -d constructai -t -c "SELECT count(*) FROM punch_list_items" 2>/dev/null | tr -d ' \n')
check "Punch list in DB > 0" "1" "$([ "$PLI_CT" -gt 0 ] && echo 1 || echo 0)"

echo ""
echo "=== FILTER TESTS ==="
OPEN_CT=$(curl -s "$API/api/v1/projects/$PID/rfis?status=open" -b "$COOKIES" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('data',[])))" 2>/dev/null)
check "RFI status=open has results" "1" "$([ "$OPEN_CT" -gt 0 ] && echo 1 || echo 0)"

HIGH_CT=$(curl -s "$API/api/v1/projects/$PID/rfis?priority=high" -b "$COOKIES" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('data',[])))" 2>/dev/null)
check "RFI priority=high has results" "1" "$([ "$HIGH_CT" -gt 0 ] && echo 1 || echo 0)"

SRCH_CT=$(curl -s "$API/api/v1/projects/$PID/rfis?search=concrete" -b "$COOKIES" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('data',[])))" 2>/dev/null)
check "RFI search=concrete has results" "1" "$([ "$SRCH_CT" -gt 0 ] && echo 1 || echo 0)"

echo ""
echo "=== EXPORT TESTS ==="
CODE=$(curl -s -o /dev/null -w "%{http_code}" "$API/api/v1/projects/$PID/rfis/export" -b "$COOKIES")
check "RFI export -> 200" "200" "$CODE"

echo ""
echo "=== SETTINGS TESTS ==="
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X PATCH "$API/api/v1/users/me" -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF" -b "$COOKIES" -d '{"full_name":"Sarah Chen-Test"}')
check "Update name -> 200" "200" "$CODE"

NAME=$(curl -s "$API/api/v1/auth/me" -b "$COOKIES" | python3 -c "import sys,json; print(json.load(sys.stdin).get('full_name',''))" 2>/dev/null)
check "Name persisted" "Sarah Chen-Test" "$NAME"

# Restore
curl -s -X PATCH "$API/api/v1/users/me" -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF" -b "$COOKIES" -d '{"full_name":"Sarah Chen"}' > /dev/null

echo ""
echo "=== TOKEN BLACKLIST ==="
curl -s -X POST "$API/api/v1/auth/logout" -H "X-CSRF-Token: $CSRF" -b "$COOKIES" > /dev/null
CODE=$(curl -s -o /dev/null -w "%{http_code}" "$API/api/v1/projects/" -b "$COOKIES")
check "Post-logout token rejected" "1" "$([ "$CODE" = "401" ] || [ "$CODE" = "403" ] && echo 1 || echo 0)"

# Re-login
login "pm@buildright.dev"

echo ""
echo "=== SAFETY STATS ==="
STATS=$(curl -s "$API/api/v1/safety/stats?project_id=$PID" -b "$COOKIES" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('total_alerts',0))" 2>/dev/null)
check "Safety total_alerts=15" "15" "$STATS"

echo ""
echo "=== EVM DATA ==="
EVM=$(curl -s "$API/api/v1/controls/evm-snapshots?project_id=$PID&limit=1" -b "$COOKIES" | python3 -c "
import sys,json
d=json.load(sys.stdin)
items = d.get('data', d.get('items', []))
if items:
    latest = items[-1]
    print(f\"{latest.get('spi',0):.2f}\")
else:
    print('none')
" 2>/dev/null)
check "Latest SPI=0.88" "0.88" "$EVM"

echo ""
echo "=== HEALTH & READINESS ==="
HEALTH=$(curl -s "$API/api/v1/health" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
check "Health -> healthy" "healthy" "$HEALTH"

READY=$(curl -s "$API/api/v1/health/ready" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('components',{}).get('database',''))" 2>/dev/null)
check "DB readiness -> healthy" "healthy" "$READY"

echo ""
echo "=== WEBHOOK CSRF EXEMPT ==="
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API/api/v1/webhooks/procore" -H "Content-Type: application/json" -d '{"event":"test"}')
check "Webhook no CSRF -> not 403" "1" "$([ "$CODE" != "403" ] && echo 1 || echo 0)"

echo ""
echo "========================================="
echo "TOTAL: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "FAILURES:"
  echo "$ISSUES" | tr '|' '\n'
fi
echo "========================================="
