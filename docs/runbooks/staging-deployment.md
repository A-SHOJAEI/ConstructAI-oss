# Staging Deployment Runbook

## Environment
- URL: https://staging.constructai.dev
- Database: PostgreSQL on RDS (staging)
- Redis: ElastiCache (staging)
- Kafka: MSK (staging)

## Deployment Steps

### 1. Build Docker Image
```bash
docker build -t constructai-api:staging -f apps/api/Dockerfile .
docker push ecr.aws/constructai/api:staging
```

### 2. Run Migrations
```bash
kubectl exec -it deploy/api -- alembic upgrade head
```

### 3. Deploy
```bash
kubectl set image deployment/api api=ecr.aws/constructai/api:staging
kubectl rollout status deployment/api
```

### 4. Verify
```bash
curl https://staging.constructai.dev/api/v1/health
```

### 5. Run Smoke Tests
```bash
pytest tests/ -m "not slow" --base-url=https://staging.constructai.dev
```

## Rollback
```bash
kubectl rollout undo deployment/api
```
