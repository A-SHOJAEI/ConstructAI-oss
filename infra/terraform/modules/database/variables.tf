# =============================================================================
# Database Module - Variables
# =============================================================================

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
}

variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID where database resources are deployed"
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for database placement"
  type        = list(string)
}

variable "db_security_group_id" {
  description = "Security group ID for the RDS instance"
  type        = string
}

variable "redis_security_group_id" {
  description = "Security group ID for the Redis cluster"
  type        = string
}

# -----------------------------------------------------------------------------
# RDS Configuration
# -----------------------------------------------------------------------------

# L-22: Bind postgres version once so parameter-group family, engine
# version, and parameter-group name all stay in lockstep when upgrading.
variable "postgres_major_version" {
  description = "Postgres major version for parameter group family (e.g. 17)"
  type        = number
  default     = 17
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.medium"
}

variable "db_name" {
  description = "Name of the default database"
  type        = string
  default     = "constructai"
}

variable "db_username" {
  description = "Master username for the RDS instance"
  type        = string
  default     = "constructai_admin"
}

variable "db_allocated_storage" {
  description = "Initial allocated storage in GB"
  type        = number
  default     = 20
}

variable "db_max_allocated_storage" {
  description = "Maximum storage for autoscaling in GB"
  type        = number
  default     = 100
}

# -----------------------------------------------------------------------------
# Redis Configuration
# -----------------------------------------------------------------------------

variable "redis_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.t3.medium"
}

variable "redis_num_shards" {
  description = "Number of cache clusters (nodes) in the replication group"
  type        = number
  default     = 1
}
