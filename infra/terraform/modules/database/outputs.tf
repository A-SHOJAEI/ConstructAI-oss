# =============================================================================
# Database Module - Outputs
# =============================================================================

# -----------------------------------------------------------------------------
# RDS PostgreSQL
# -----------------------------------------------------------------------------

output "db_endpoint" {
  description = "RDS instance endpoint (hostname)"
  value       = aws_db_instance.postgresql.address
}

output "db_port" {
  description = "RDS instance port"
  value       = aws_db_instance.postgresql.port
}

output "db_name" {
  description = "Name of the default database"
  value       = aws_db_instance.postgresql.db_name
}

output "db_instance_id" {
  description = "RDS instance identifier"
  value       = aws_db_instance.postgresql.id
}

output "db_instance_arn" {
  description = "RDS instance ARN"
  value       = aws_db_instance.postgresql.arn
}

output "db_password_secret_arn" {
  description = "ARN of the Secrets Manager secret containing the DB password"
  value       = aws_secretsmanager_secret.db_password.arn
  sensitive   = true
}

# -----------------------------------------------------------------------------
# ElastiCache Redis
# -----------------------------------------------------------------------------

output "redis_endpoint" {
  description = "Redis primary endpoint address"
  value       = aws_elasticache_replication_group.redis.primary_endpoint_address
}

output "redis_port" {
  description = "Redis port"
  value       = aws_elasticache_replication_group.redis.port
}

output "redis_replication_group_id" {
  description = "Redis replication group ID"
  value       = aws_elasticache_replication_group.redis.id
}

output "redis_replication_group_arn" {
  description = "Redis replication group ARN"
  value       = aws_elasticache_replication_group.redis.arn
}
