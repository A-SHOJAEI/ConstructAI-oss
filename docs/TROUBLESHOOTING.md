# Troubleshooting Guide

## Common Issues

### Application won't start

**Symptom:** `docker compose up` fails or services don't become healthy.

**Solutions:**
1. Verify all required environment variables are set (see `.env.example`)
2. Ensure PostgreSQL and Redis are running: `docker compose ps`
3. Check logs: `docker compose logs api` or `docker compose logs web`
4. Verify database migrations: `docker compose exec api alembic upgrade head`

### Login fails with "Invalid credentials"

**Solutions:**
1. Verify the email is correct (case-sensitive)
2. Check if the account is active (admin can verify in Admin > Users)
3. If using SSO, ensure the SSO provider is configured
4. Try resetting your password via "Forgot password"

### MFA verification fails

**Solutions:**
1. Ensure your authenticator app's time is synced
2. Try the next code (codes rotate every 30 seconds)
3. If locked out, contact an admin to reset MFA

### Camera feed not connecting

**Solutions:**
1. Verify the RTSP URL is accessible from the API server
2. Check SSRF validation isn't blocking the camera IP
3. Ensure the camera is on a private network (public URLs are blocked for security)
4. Check camera status: `GET /api/v1/cameras/`

### Document upload fails

**Solutions:**
1. Verify the file is under the size limit (50 MB)
2. Check the file type is supported (PDF, IFC, CSV, DOCX)
3. Ensure the file passes magic byte validation (renamed files are rejected)
4. Check API logs for processing errors

### Schedule import fails

**Solutions:**
1. Verify the file format (.xer, .pmxml, .mpp, .xml)
2. For P6 files, ensure they were exported from a compatible P6 version
3. Check that activities have valid IDs and durations
4. Review the import error details in the API response

### Slow performance

**Solutions:**
1. Check Redis connection: `redis-cli ping`
2. Review database query performance: check `pg_stat_statements`
3. Ensure proper indexing on frequently queried columns
4. Check Prometheus metrics at `/metrics` for bottlenecks
5. Scale API replicas if CPU utilization is high

### WebSocket connection drops

**Solutions:**
1. Verify the WebSocket URL includes the authentication token
2. Check for proxy/load balancer timeout settings (increase to 300s)
3. Ensure CORS is configured for the WebSocket origin
4. Check rate limiting on WebSocket connections

## Environment-Specific Issues

### Development

- **Hot reload not working:** Ensure `ENVIRONMENT=development` in `.env`
- **TypeScript errors:** Run `npx tsc --noEmit` to check for type issues
- **Test failures:** Run `npx vitest run` for frontend, `pytest` for backend

### Production

- **TLS certificate issues:** Check cert-manager logs and ClusterIssuer status
- **Helm deployment failures:** Run `helm status constructai -n constructai`
- **Pod crashes:** Check `kubectl logs -n constructai <pod-name> --previous`

## Getting Help

- **Documentation:** Check the `docs/` directory for detailed guides
- **In-app help:** Press the help button (?) in the application header
- **Keyboard shortcuts:** Press `Shift+?` for available shortcuts
- **Support:** Email support@constructai.dev
- **Issues:** File bugs at the project's issue tracker
