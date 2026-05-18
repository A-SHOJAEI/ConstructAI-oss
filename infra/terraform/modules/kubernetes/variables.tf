# =============================================================================
# Kubernetes (EKS) Module - Variables
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
  description = "VPC ID where EKS is deployed"
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for EKS cluster and nodes"
  type        = list(string)
}

variable "eks_node_instance_type" {
  description = "EC2 instance type for EKS worker nodes"
  type        = string
  default     = "t3.large"
}

variable "eks_min_nodes" {
  description = "Minimum number of nodes in the node group"
  type        = number
  default     = 2
}

variable "eks_max_nodes" {
  description = "Maximum number of nodes in the node group"
  type        = number
  default     = 6
}

variable "eks_desired_nodes" {
  description = "Desired number of nodes in the node group"
  type        = number
  default     = 2
}
