#!/bin/bash
API="http://localhost:8000"
COOKIES="/tmp/reg_cookies.txt"
PASS=0; FAIL=0
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
    FAIL=$((FAIL+1)); echo "  FAIL: $name (exp=$expected got=$actual)"
  fi
}

echo "============================================"
echo "  REGRESSION TEST SUITE"
echo "============================================"

echo ""
echo "=== 1. HEALTH & INFRASTRUCTURE ==="
check "Backend health" "200" "$(curl -s -o /dev/null -w '%{http_code}' $API/api/v1/health)"
check "DB readiness" "healthy" "$(curl -s $API/api/v1/health/ready | python3 -c 'import sys,json; print(json.load(sys.stdin).get(\"components\",{}).get(\"database\",\"\"))' 2>/dev/null)"
check "Frontend health" "200" "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3100)"
check "API docs" "200" "$(curl -s -o /dev/null -w '%{http_code}' $API/docs)"

echo ""
echo "=== 2. AUTH FLOW ==="
login "pm@buildright.dev"
check "Login returns 200" "200" "$(printf '{"email":"pm@buildright.dev","password":"Demo2026!"}' | curl -s -o /dev/null -w '%{http_code}' -X POST $API/api/v1/auth/login -H 'Content-Type: application/json' -d @-)"
check "Wrong password returns 401" "401" "$(printf '{"email":"pm@buildright.dev","password":"wrong"}' | curl -s -o /dev/null -w '%{http_code}' -X POST $API/api/v1/auth/login -H 'Content-Type: application/json' -d @-)"
check "Auth/me returns 200" "200" "$(curl -s -o /dev/null -w '%{http_code}' $API/api/v1/auth/me -b $COOKIES)"
check "Unauth returns 403" "403" "$(curl -s -o /dev/null -w '%{http_code}' $API/api/v1/projects/)"
check "POST no CSRF returns 403" "403" "$(curl -s -o /dev/null -w '%{http_code}' -X POST $API/api/v1/projects/ -H 'Content-Type: application/json' -b $COOKIES -d '{}')"
check "Login CSRF-exempt" "200" "$(printf '{"email":"pm@buildright.dev","password":"Demo2026!"}' | curl -s -o /dev/null -w '%{http_code}' -X POST $API/api/v1/auth/login -H 'Content-Type: application/json' -d @-)"

echo ""
echo "=== 3. PROJECTS ==="
login "pm@buildright.dev"
PROJ_FMT=$(curl -s "$API/api/v1/projects/" -b $COOKIES | python3 -c "import sys,json; d=json.load(sys.stdin); print('data' if 'data' in d else 'other')" 2>/dev/null)
check "Projects returns {data:[...]}" "data" "$PROJ_FMT"
PROJ_CT=$(curl -s "$API/api/v1/projects/" -b $COOKIES | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('data',[])))" 2>/dev/null)
check "Projects has data" "1" "$([ "$PROJ_CT" -gt 0 ] && echo 1 || echo 0)"

echo ""
echo "=== 4. RFI CRUD + PERSISTENCE ==="
# Create RFI
RFI_RESP=$(curl -s -X POST "$API/api/v1/projects/$PID/rfis" -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF" -b $COOKIES -d '{"subject":"Regression Test RFI","question":"Does RFI creation still work after all fixes?","priority":"normal"}')
RFI_NUM=$(echo "$RFI_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('rfi_number','ERROR'))" 2>/dev/null)
check "RFI created" "1" "$(echo $RFI_NUM | grep -q 'RFI' && echo 1 || echo 0)"

# Verify persistence
sleep 1
DB_CT=$(docker exec constructai-postgres psql -U constructai -d constructai -t -c "SELECT count(*) FROM rfis WHERE rfi_number='$RFI_NUM'" 2>/dev/null | tr -d ' \n')
check "RFI persisted in DB" "1" "$DB_CT"

# RFI list
RFI_FMT=$(curl -s "$API/api/v1/projects/$PID/rfis?" -b $COOKIES | python3 -c "import sys,json; print('data' if 'data' in json.load(sys.stdin) else 'other')" 2>/dev/null)
check "RFIs returns {data:[...]}" "data" "$RFI_FMT"

# RFI filters
OPEN_CT=$(curl -s "$API/api/v1/projects/$PID/rfis?status=open" -b $COOKIES | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('data',[])))" 2>/dev/null)
check "RFI filter status=open" "1" "$([ "$OPEN_CT" -gt 0 ] && echo 1 || echo 0)"

HIGH_CT=$(curl -s "$API/api/v1/projects/$PID/rfis?priority=high" -b $COOKIES | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('data',[])))" 2>/dev/null)
check "RFI filter priority=high" "1" "$([ "$HIGH_CT" -gt 0 ] && echo 1 || echo 0)"

# RFI respond
RFI_ID=$(echo "$RFI_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
RESP_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/api/v1/projects/$PID/rfis/$RFI_ID/respond" -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF" -b $COOKIES -d '{"response_text":"Regression test response"}')
check "RFI respond returns 201" "201" "$RESP_CODE"

# Verify response persisted
sleep 1
RESP_CT=$(docker exec constructai-postgres psql -U constructai -d constructai -t -c "SELECT count(*) FROM rfi_responses WHERE rfi_id='$RFI_ID'" 2>/dev/null | tr -d ' \n')
check "RFI response persisted" "1" "$RESP_CT"

# RFI close
CLOSE_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/api/v1/projects/$PID/rfis/$RFI_ID/close" -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF" -b $COOKIES -d '{"answer":"Closed in regression test"}')
check "RFI close returns 200" "200" "$CLOSE_CODE"

CLOSED_STATUS=$(docker exec constructai-postgres psql -U constructai -d constructai -t -c "SELECT status FROM rfis WHERE id='$RFI_ID'" 2>/dev/null | tr -d ' \n')
check "RFI status=closed in DB" "closed" "$CLOSED_STATUS"

# RFI export
check "RFI export" "200" "$(curl -s -o /dev/null -w '%{http_code}' $API/api/v1/projects/$PID/rfis/export -b $COOKIES)"

echo ""
echo "=== 5. SUBMITTALS & PUNCH LIST ==="
# Create submittal
SUB_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/api/v1/projects/$PID/submittals" -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF" -b $COOKIES -d '{"title":"Regression Test Submittal","submittal_type":"shop_drawing","priority":"normal"}')
check "Submittal create" "1" "$([ "$SUB_CODE" = "201" ] || [ "$SUB_CODE" = "200" ] && echo 1 || echo 0)"

# Create punch list
PLI_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/api/v1/projects/$PID/punch-list" -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF" -b $COOKIES -d '{"description":"Regression test punch item - drywall repair needed","location":"Level 3","category":"drywall"}')
check "Punch list create" "1" "$([ "$PLI_CODE" = "201" ] || [ "$PLI_CODE" = "200" ] && echo 1 || echo 0)"

echo ""
echo "=== 6. EVM & CONTROLS ==="
SPI=$(curl -s "$API/api/v1/controls/evm-snapshots?project_id=$PID&limit=1" -b $COOKIES | python3 -c "import sys,json; d=json.load(sys.stdin); items=d.get('data',d.get('items',[])); print(f'{float(items[-1][\"spi\"]):.2f}' if items else 'none')" 2>/dev/null)
check "EVM SPI=0.88" "0.88" "$SPI"

check "S-curve endpoint" "200" "$(curl -s -o /dev/null -w '%{http_code}' $API/api/v1/controls/s-curve/$PID -b $COOKIES)"

echo ""
echo "=== 7. SAFETY ==="
ALERTS=$(curl -s "$API/api/v1/safety/stats?project_id=$PID" -b $COOKIES | python3 -c "import sys,json; print(json.load(sys.stdin).get('total_alerts',0))" 2>/dev/null)
check "Safety total_alerts=15" "15" "$ALERTS"

echo ""
echo "=== 8. SETTINGS & PROFILE ==="
# Update name
NAME_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X PATCH "$API/api/v1/users/me" -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF" -b $COOKIES -d '{"full_name":"Sarah Chen-Regression"}')
check "Update name" "200" "$NAME_CODE"

NAME=$(curl -s "$API/api/v1/auth/me" -b $COOKIES | python3 -c "import sys,json; print(json.load(sys.stdin).get('full_name',''))" 2>/dev/null)
check "Name persisted" "Sarah Chen-Regression" "$NAME"

# Restore
curl -s -X PATCH "$API/api/v1/users/me" -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF" -b $COOKIES -d '{"full_name":"Sarah Chen"}' > /dev/null

# Notification prefs
check "Notif prefs GET" "200" "$(curl -s -o /dev/null -w '%{http_code}' $API/api/v1/users/me/notification-preferences -b $COOKIES)"
check "Notif prefs PATCH" "200" "$(curl -s -o /dev/null -w '%{http_code}' -X PATCH $API/api/v1/users/me/notification-preferences -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" -b $COOKIES -d '{\"daily_digest\":true}')"

echo ""
echo "=== 9. SECURITY ==="
check "Forgot password (no enum)" "200" "$(printf '{"email":"nobody@example.com"}' | curl -s -o /dev/null -w '%{http_code}' -X POST $API/api/v1/auth/forgot-password -H 'Content-Type: application/json' -d @-)"

# Token blacklist
curl -s -X POST "$API/api/v1/auth/logout" -H "X-CSRF-Token: $CSRF" -b $COOKIES > /dev/null
BLACKLIST_CODE=$(curl -s -o /dev/null -w '%{http_code}' "$API/api/v1/projects/" -b $COOKIES)
check "Token blacklisted after logout" "1" "$([ "$BLACKLIST_CODE" = "401" ] || [ "$BLACKLIST_CODE" = "403" ] && echo 1 || echo 0)"

# Re-login for remaining tests
login "pm@buildright.dev"

echo ""
echo "=== 10. DOCUMENTS ==="
DOC_FMT=$(curl -s "$API/api/v1/documents/?project_id=$PID" -b $COOKIES | python3 -c "import sys,json; print('data' if 'data' in json.load(sys.stdin) else 'other')" 2>/dev/null)
check "Documents returns {data:[...]}" "data" "$DOC_FMT"

echo ""
echo "=== 11. DAILY LOGS ==="
DL_CT=$(curl -s "$API/api/v1/projects/$PID/daily-logs?status=draft" -b $COOKIES | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('data',json.load(open('/dev/null')) if False else [])))" 2>/dev/null || echo 0)
check "Daily logs draft" "1" "$([ "${DL_CT:-0}" -gt 0 ] && echo 1 || echo 0)"

echo ""
echo "=== 12. MEETINGS ==="
MT_CT=$(curl -s "$API/api/v1/communication/meetings?project_id=$PID&limit=100" -b $COOKIES | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('data',json.load(open('/dev/null')) if False else [])))" 2>/dev/null || echo 0)
check "Meetings exist" "1" "$([ "${MT_CT:-0}" -gt 0 ] && echo 1 || echo 0)"

echo ""
echo "=== 13. CHANGE ORDERS ==="
CO_CT=$(curl -s "$API/api/v1/controls/change-orders?project_id=$PID&limit=100" -b $COOKIES | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',d.get('items',[]))))" 2>/dev/null)
check "Change orders exist" "1" "$([ "$CO_CT" -gt 0 ] && echo 1 || echo 0)"

echo ""
echo "=== 14. WEBHOOK CSRF EXEMPT ==="
check "Webhook no CSRF" "1" "$([ \"$(curl -s -o /dev/null -w '%{http_code}' -X POST $API/api/v1/webhooks/procore -H 'Content-Type: application/json' -d '{\"event\":\"test\"}')\" != '403' ] && echo 1 || echo 0)"

echo ""
echo "=== 15. PAGES RENDER (Frontend) ==="
check "Login page" "200" "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3100/login)"
check "Forgot password" "200" "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3100/forgot-password)"
check "Reset password" "200" "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3100/reset-password)"

echo ""
echo "============================================"
echo "  RESULTS: $PASS passed, $FAIL failed"
echo "============================================"
if [ "$FAIL" -gt 0 ]; then
  echo "  WARNING: $FAIL tests failed!"
fi
