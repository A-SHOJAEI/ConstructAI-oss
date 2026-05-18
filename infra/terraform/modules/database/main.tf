# =============================================================================
# ConstructAI Platform - Database Module
# =============================================================================
# Creates RDS PostgreSQL (with TimescaleDB) and ElastiCache Redis instances.
# =============================================================================

# -----------------------------------------------------------------------------
# Random password for RDS (stored in Terraform state -- use Secrets Manager
# for production secret rotation)
# -----------------------------------------------------------------------------

resource "random_password" "db_password" {
  length           = 32
  special          = true
  override_special = "!#$%^&*()-_=+[]{}|:,.<>?"
}

resource "aws_secretsmanager_secret" "db_password" {
  name                    = "${var.project_name}-${var.environment}-db-password"
  description             = "RDS PostgreSQL master password for ${var.project_name}"
  recovery_window_in_days = var.environment == "production" ? 30 : 0

  tags = {
    Name = "${var.project_name}-${var.environment}-db-password"
  }
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id     = aws_secretsmanager_secret.db_password.id
  secret_string = random_password.db_password.result
}

# -----------------------------------------------------------------------------
# RDS Subnet Group
# -----------------------------------------------------------------------------

resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-${var.environment}-db-subnet"
  subnet_ids = var.private_subnet_ids

  tags = {
    Name = "${var.project_name}-${var.environment}-db-subnet-group"
  }
}

# -----------------------------------------------------------------------------
# RDS Parameter Group (configured for TimescaleDB compatibility)
# -----------------------------------------------------------------------------

resource "aws_db_parameter_group" "postgresql" {
  name   = "${var.project_name}-${var.environment}-pg${var.postgres_major_version}-timescaledb"
  family = "postgres${var.postgres_major_version}"

  description = "PostgreSQL ${var.postgres_major_version} parameter group with TimescaleDB-compatible settings"

  parameter {
    name  = "shared_preload_libraries"
    value = "pg_stat_statements"
  }

  parameter {
    name  = "max_connections"
    value = var.environment == "production" ? "200" : "100"
  }

  parameter {
    name  = "work_mem"
    value = var.environment == "production" ? "65536" : "16384"
  }

  parameter {
    name  = "maintenance_work_mem"
    value = var.environment == "production" ? "524288" : "131072"
  }

  parameter {
    name  = "effective_cache_size"
    value = var.environment == "production" ? "3145728" : "786432"
  }

  parameter {
    name         = "log_min_duration_statement"
    value        = "1000"
    apply_method = "pending-reboot"
  }

  parameter {
    name  = "log_statement"
    value = "ddl"
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-pg17-params"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# -----------------------------------------------------------------------------
# RDS PostgreSQL Instance
# -----------------------------------------------------------------------------

resource "aws_db_instance" "postgresql" {
  identifier = "${var.project_name}-${var.environment}-postgres"

  engine         = "postgres"
  engine_version = "17"
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = var.db_name
  username = var.db_username
  password = random_password.db_password.result

  db_subnet_group_name = aws_db_subnet_group.main.name
  parameter_group_name = aws_db_parameter_group.postgresql.name

  vpc_security_group_ids = [var.db_security_group_id]

  multi_az            = var.environment == "production" ? true : false
  publicly_accessible = false

  # Backup configuration
  backup_retention_period = var.environment == "production" ? 30 : 7
  backup_window           = "03:00-04:00"
  maintenance_window      = "Mon:04:00-Mon:05:00"

  # Snapshot and deletion settings
  skip_final_snapshot       = var.environment == "production" ? false : true
  final_snapshot_identifier = var.environment == "production" ? "${var.project_name}-${var.environment}-final-snapshot" : null
  deletion_protection       = var.environment == "production" ? true : false
  copy_tags_to_snapshot     = true

  # Monitoring
  performance_insights_enabled          = true
  performance_insights_retention_period = var.environment == "production" ? 731 : 7

  # Logging
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  tags = {
    Name = "${var.project_name}-${var.environment}-postgres"
  }
}

# H-24: CloudWatch log groups for RDS PostgreSQL with explicit retention.
# Without these, RDS creates log groups on-demand with `retention_in_days = 0`
# (never expire) — logs grow unbounded. Production retains longer for
# compliance; dev/staging stays tight.
resource "aws_cloudwatch_log_group" "rds_postgresql" {
  for_each          = toset(["postgresql", "upgrade"])
  name              = "/aws/rds/instance/${aws_db_instance.postgresql.identifier}/${each.value}"
  retention_in_days = var.environment == "production" ? 90 : 14

  tags = {
    Name        = "${var.project_name}-${var.environment}-rds-${each.value}"
    Environment = var.environment
  }
}

# -----------------------------------------------------------------------------
# ElastiCache Subnet Group
# -----------------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.project_name}-${var.environment}-redis-subnet"
  subnet_ids = var.private_subnet_ids

  tags = {
    Name = "${var.project_name}-${var.environment}-redis-subnet-group"
  }
}

# -----------------------------------------------------------------------------
# ElastiCache Redis
# -----------------------------------------------------------------------------

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "${var.project_name}-${var.environment}-redis"
  description          = "Redis cluster for ${var.project_name} ${var.environment}"

  engine         = "redis"
  engine_version = "7.1"
  node_type      = var.redis_node_type
  # M-50: ElastiCache with `transit_encryption_enabled = true` listens on a
  # single port for TLS connections — 6379 remains the external listener
  # (transit encryption is layer-7 TLS on top of the same port). The
  # docker-compose.production.yml Redis uses 6380 because that variant
  # runs plain+TLS dual-listener mode. Document explicitly so the port
  # mismatch doesn't look like a bug.
  port                 = 6379
  parameter_group_name = "default.redis7"

  # Single node for staging, multi-AZ with replicas for production
  num_cache_clusters = var.environment == "production" ? var.redis_num_shards : 1

  automatic_failover_enabled = var.environment == "production" ? true : false
  multi_az_enabled           = var.environment == "production" ? true : false

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [var.redis_security_group_id]

  # Encryption
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  # Maintenance
  maintenance_window       = "Tue:04:00-Tue:05:00"
  snapshot_retention_limit = var.environment == "production" ? 7 : 1
  snapshot_window          = "02:00-03:00"

  # Auto minor version upgrade
  auto_minor_version_upgrade = true

  tags = {
    Name = "${var.project_name}-${var.environment}-redis"
  }
}
