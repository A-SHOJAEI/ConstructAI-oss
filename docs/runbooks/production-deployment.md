# Production Deployment Runbook

## Pre-Deployment Checklist
- [ ] All tests passing in CI
- [ ] Staging deployment verified
- [ ] Database migration reviewed and approved
- [ ] Rollback plan documented
- [ ] On-call engineer notified

## Deployment Steps

### 1. Create Release
```bash
git tag v1.x.x
git push origin v1.x.x
# CI builds and pushes production image
```

### 2. Database Migration (if needed)
```bash
# Take backup first
pg_dump constructai > backup_$(date +%Y%m%d).sql
# Run migration
kubectl exec -it deploy/api -- alembic upgrade head
```

### 3. Deploy with Canary
```bash
# Deploy to 5% of traffic
kubectl apply -f k8s/canary-deployment.yaml
# Monitor for 30 minutes
# If healthy, promote to full deployment
kubectl apply -f k8s/production-deployment.yaml
```

### 4. Post-Deployment Verification
```bash
# Check health
curl https://api.constructai.dev/api/v1/health
# Check Grafana dashboards
open https://grafana.constructai.dev
# Verify error rates in Prometheus
```

## Rollback
```bash
kubectl rollout undo deployment/api
# If migration needs rollback:
kubectl exec -it deploy/api -- alembic downgrade -1
```
