# =============================================================================
# ConstructAI Platform - Production Environment
# =============================================================================
# Larger instances, multi-AZ, high-availability configuration.
# =============================================================================

environment  = "production"
aws_region   = "us-east-1"
project_name = "constructai"

# Database - production-grade instance with multi-AZ
db_instance_class = "db.r6g.xlarge"

# EKS - larger nodes with higher scaling limits for production traffic
eks_node_instance_type = "m6i.xlarge"
eks_min_nodes          = 3
eks_max_nodes          = 10
eks_desired_nodes      = 3
