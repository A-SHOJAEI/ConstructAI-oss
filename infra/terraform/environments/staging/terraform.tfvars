# =============================================================================
# ConstructAI Platform - Staging Environment
# =============================================================================
# Smaller instances, single-AZ, cost-optimized for development and testing.
# =============================================================================

environment = "staging"
aws_region  = "us-east-1"
project_name = "constructai"

# Database - smaller instance for staging workloads
db_instance_class = "db.t3.medium"

# EKS - minimal node count with smaller instances
eks_node_instance_type = "t3.medium"
eks_min_nodes          = 1
eks_max_nodes          = 3
eks_desired_nodes      = 2
