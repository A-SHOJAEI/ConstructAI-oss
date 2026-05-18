# ConstructAI Multi-Region Deployment Module
# Deploys the ConstructAI platform across multiple AWS regions
# for high availability and disaster recovery.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

variable "project_name" {
  type    = string
  default = "constructai"
}

variable "environment" {
  type    = string
  default = "production"
}

variable "primary_region" {
  type    = string
  default = "us-east-1"
}

variable "secondary_region" {
  type    = string
  default = "us-west-2"
}

variable "db_instance_class" {
  type    = string
  default = "db.r6g.xlarge"
}

variable "eks_node_instance_type" {
  type    = string
  default = "m6i.xlarge"
}

variable "eks_min_nodes" {
  type    = number
  default = 2
}

variable "eks_max_nodes" {
  type    = number
  default = 10
}

# Primary region provider
provider "aws" {
  region = var.primary_region
  alias  = "primary"

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# Secondary region provider
provider "aws" {
  region = var.secondary_region
  alias  = "secondary"

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# --- VPC ---

module "vpc_primary" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  providers = { aws = aws.primary }

  name = "${var.project_name}-${var.environment}-primary"
  cidr = "10.0.0.0/16"

  azs             = ["${var.primary_region}a", "${var.primary_region}b", "${var.primary_region}c"]
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]

  enable_nat_gateway   = true
  single_nat_gateway   = false
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    "kubernetes.io/cluster/${var.project_name}-${var.environment}" = "shared"
  }
}

module "vpc_secondary" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  providers = { aws = aws.secondary }

  name = "${var.project_name}-${var.environment}-secondary"
  cidr = "10.1.0.0/16"

  azs             = ["${var.secondary_region}a", "${var.secondary_region}b", "${var.secondary_region}c"]
  private_subnets = ["10.1.1.0/24", "10.1.2.0/24", "10.1.3.0/24"]
  public_subnets  = ["10.1.101.0/24", "10.1.102.0/24", "10.1.103.0/24"]

  enable_nat_gateway   = true
  single_nat_gateway   = false
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    "kubernetes.io/cluster/${var.project_name}-${var.environment}-dr" = "shared"
  }
}

# --- RDS Aurora Global Database ---

resource "aws_rds_global_cluster" "main" {
  provider = aws.primary

  global_cluster_identifier = "${var.project_name}-${var.environment}-global"
  engine                    = "aurora-postgresql"
  engine_version            = "15.4"
  storage_encrypted         = true
}

resource "aws_rds_cluster" "primary" {
  provider = aws.primary

  cluster_identifier        = "${var.project_name}-${var.environment}-primary"
  global_cluster_identifier = aws_rds_global_cluster.main.id
  engine                    = aws_rds_global_cluster.main.engine
  engine_version            = aws_rds_global_cluster.main.engine_version
  db_subnet_group_name      = aws_db_subnet_group.primary.name
  vpc_security_group_ids    = [aws_security_group.rds_primary.id]
  master_username           = "constructai"
  manage_master_user_password = true
  storage_encrypted         = true
  backup_retention_period   = 7
  preferred_backup_window   = "03:00-04:00"
}

resource "aws_rds_cluster_instance" "primary" {
  provider = aws.primary
  count    = 2

  identifier         = "${var.project_name}-${var.environment}-primary-${count.index}"
  cluster_identifier = aws_rds_cluster.primary.id
  instance_class     = var.db_instance_class
  engine             = aws_rds_global_cluster.main.engine
  engine_version     = aws_rds_global_cluster.main.engine_version
}

resource "aws_rds_cluster" "secondary" {
  provider   = aws.secondary
  depends_on = [aws_rds_cluster.primary]

  cluster_identifier        = "${var.project_name}-${var.environment}-secondary"
  global_cluster_identifier = aws_rds_global_cluster.main.id
  engine                    = aws_rds_global_cluster.main.engine
  engine_version            = aws_rds_global_cluster.main.engine_version
  db_subnet_group_name      = aws_db_subnet_group.secondary.name
  vpc_security_group_ids    = [aws_security_group.rds_secondary.id]
  storage_encrypted         = true
}

resource "aws_rds_cluster_instance" "secondary" {
  provider = aws.secondary
  count    = 1

  identifier         = "${var.project_name}-${var.environment}-secondary-${count.index}"
  cluster_identifier = aws_rds_cluster.secondary.id
  instance_class     = var.db_instance_class
  engine             = aws_rds_global_cluster.main.engine
  engine_version     = aws_rds_global_cluster.main.engine_version
}

# --- Supporting Resources (subnet groups, security groups) ---

resource "aws_db_subnet_group" "primary" {
  provider = aws.primary

  name       = "${var.project_name}-${var.environment}-primary"
  subnet_ids = module.vpc_primary.private_subnets
}

resource "aws_db_subnet_group" "secondary" {
  provider = aws.secondary

  name       = "${var.project_name}-${var.environment}-secondary"
  subnet_ids = module.vpc_secondary.private_subnets
}

resource "aws_security_group" "rds_primary" {
  provider = aws.primary

  name_prefix = "${var.project_name}-rds-"
  vpc_id      = module.vpc_primary.vpc_id

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = module.vpc_primary.private_subnets_cidr_blocks
  }
}

resource "aws_security_group" "rds_secondary" {
  provider = aws.secondary

  name_prefix = "${var.project_name}-rds-"
  vpc_id      = module.vpc_secondary.vpc_id

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = module.vpc_secondary.private_subnets_cidr_blocks
  }
}

# --- Outputs ---

output "primary_vpc_id" {
  value = module.vpc_primary.vpc_id
}

output "secondary_vpc_id" {
  value = module.vpc_secondary.vpc_id
}

output "primary_db_endpoint" {
  value = aws_rds_cluster.primary.endpoint
}

output "secondary_db_endpoint" {
  value = aws_rds_cluster.secondary.reader_endpoint
}
