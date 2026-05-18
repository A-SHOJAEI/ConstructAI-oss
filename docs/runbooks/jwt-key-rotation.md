# JWT Key Rotation Runbook

## When to Rotate
- Suspected key compromise
- Routine rotation (recommended: every 90 days)
- Personnel changes (employees with key access leaving)

## Prerequisites
- Platform admin account with MFA enabled
- Redis must be running and accessible
- Generate a new key: `python -c "import secrets; print(secrets.token_urlsafe(48))"`

## Procedure

1. Generate new key:
   ```bash
   NEW_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
   echo "New key: $NEW_KEY"
   ```

2. Call rotation endpoint:
   ```bash
   curl -X POST https://api.your-deployment.example.com/api/v1/admin/jwt/rotate \
     -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H "Content-Type: application/json" \
     -d "{\"new_key\": \"$NEW_KEY\"}"
   ```

3. Update environment variable:
   ```bash
   # Update in your secrets manager / K8s secret
   kubectl create secret generic constructai-jwt-secret \
     --from-literal=JWT_SECRET_KEY=$NEW_KEY \
     --dry-run=client -o yaml | kubectl apply -f -
   ```

4. Restart API pods (rolling restart):
   ```bash
   kubectl rollout restart deployment constructai-api -n constructai
   ```

## Grace Period
- Previous key remains valid for 7 days (refresh token lifetime)
- All existing access tokens (30 min lifetime) will expire naturally
- Refresh tokens issued with the old key will still work during the grace period

## Verification
```bash
# Verify new tokens use new key version
curl -s https://api.your-deployment.example.com/api/v1/auth/me \
  -H "Authorization: Bearer $NEW_TOKEN" | jq .
```

## Rollback
If the new key causes issues:
1. The previous key is stored in Redis as `cai:jwt:previous_key`
2. Set `JWT_SECRET_KEY` back to the previous key value
3. Restart API pods
