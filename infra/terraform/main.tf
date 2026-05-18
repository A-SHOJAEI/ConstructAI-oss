# =============================================================================
# ConstructAI Platform - Root Terraform Configuration
# =============================================================================
# This root module orchestrates all infrastructure components for the
# ConstructAI construction management platform on AWS.
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "constructai-terraform-state"
    key            = "infrastructure/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "constructai-terraform-locks"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# Data Sources
# -----------------------------------------------------------------------------

data "aws_availability_zones" "available" {
  state = "available"
}

# -----------------------------------------------------------------------------
# Networking Module
# -----------------------------------------------------------------------------

module "networking" {
  source = "./modules/networking"

  project_name = var.project_name
  environment  = var.environment
  vpc_cidr     = "10.0.0.0/16"

  availability_zones = slice(data.aws_availability_zones.available.names, 0, 3)

  public_subnet_cidrs = [
    "10.0.1.0/24",
    "10.0.2.0/24",
    "10.0.3.0/24",
  ]

  private_subnet_cidrs = [
    "10.0.10.0/24",
    "10.0.11.0/24",
    "10.0.12.0/24",
  ]
}

# -----------------------------------------------------------------------------
# Database Module
# -----------------------------------------------------------------------------

module "database" {
  source = "./modules/database"

  project_name = var.project_name
  environment  = var.environment

  vpc_id              = module.networking.vpc_id
  private_subnet_ids  = module.networking.private_subnet_ids
  db_security_group_id    = module.networking.db_security_group_id
  redis_security_group_id = module.networking.redis_security_group_id

  db_instance_class       = var.db_instance_class
  db_name                 = "constructai"
  db_username             = "constructai_admin"
  db_allocated_storage    = var.environment == "production" ? 100 : 20
  db_max_allocated_storage = var.environment == "production" ? 500 : 50

  redis_node_type  = var.environment == "production" ? "cache.r6g.large" : "cache.t3.medium"
  redis_num_shards = var.environment == "production" ? 3 : 1
}

# -----------------------------------------------------------------------------
# Kubernetes (EKS) Module
# -----------------------------------------------------------------------------

module "kubernetes" {
  source = "./modules/kubernetes"

  project_name = var.project_name
  environment  = var.environment

  vpc_id             = module.networking.vpc_id
  private_subnet_ids = module.networking.private_subnet_ids

  eks_node_instance_type = var.eks_node_instance_type
  eks_min_nodes          = var.eks_min_nodes
  eks_max_nodes          = var.eks_max_nodes
  eks_desired_nodes      = var.eks_desired_nodes
}

# -----------------------------------------------------------------------------
# Storage Module
# -----------------------------------------------------------------------------

module "storage" {
  source = "./modules/storage"

  project_name = var.project_name
  environment  = var.environment
}
