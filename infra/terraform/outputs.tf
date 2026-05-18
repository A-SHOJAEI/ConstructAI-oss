# =============================================================================
# ConstructAI Platform - Root Outputs
# =============================================================================

# -----------------------------------------------------------------------------
# Database Outputs
# -----------------------------------------------------------------------------

output "database_endpoint" {
  description = "PostgreSQL RDS instance endpoint"
  value       = module.database.db_endpoint
}

output "database_port" {
  description = "PostgreSQL RDS instance port"
  value       = module.database.db_port
}

output "redis_endpoint" {
  description = "ElastiCache Redis primary endpoint"
  value       = module.database.redis_endpoint
}

output "redis_port" {
  description = "ElastiCache Redis port"
  value       = module.database.redis_port
}

# -----------------------------------------------------------------------------
# Kubernetes Outputs
# -----------------------------------------------------------------------------

output "eks_cluster_endpoint" {
  description = "EKS cluster API server endpoint"
  value       = module.kubernetes.cluster_endpoint
}

output "eks_cluster_name" {
  description = "EKS cluster name"
  value       = module.kubernetes.cluster_name
}

output "eks_cluster_certificate_authority" {
  description = "EKS cluster certificate authority data"
  value       = module.kubernetes.cluster_certificate_authority
  sensitive   = true
}

# -----------------------------------------------------------------------------
# Storage Outputs
# -----------------------------------------------------------------------------

output "s3_bucket_name" {
  description = "S3 bucket name for document storage"
  value       = module.storage.documents_bucket_name
}

output "s3_bucket_arn" {
  description = "S3 bucket ARN for document storage"
  value       = module.storage.documents_bucket_arn
}

output "ecr_repository_urls" {
  description = "ECR repository URLs for container images"
  value       = module.storage.ecr_repository_urls
}

# -----------------------------------------------------------------------------
# Networking Outputs
# -----------------------------------------------------------------------------

output "vpc_id" {
  description = "VPC ID"
  value       = module.networking.vpc_id
}

output "private_subnet_ids" {
  description = "Private subnet IDs"
  value       = module.networking.private_subnet_ids
}

output "public_subnet_ids" {
  description = "Public subnet IDs"
  value       = module.networking.public_subnet_ids
}
