# =============================================================================
# Storage Module - Outputs
# =============================================================================

# -----------------------------------------------------------------------------
# S3 Bucket
# -----------------------------------------------------------------------------

output "documents_bucket_name" {
  description = "Name of the S3 bucket for document storage"
  value       = aws_s3_bucket.documents.bucket
}

output "documents_bucket_arn" {
  description = "ARN of the S3 bucket for document storage"
  value       = aws_s3_bucket.documents.arn
}

output "documents_bucket_domain_name" {
  description = "Domain name of the S3 bucket"
  value       = aws_s3_bucket.documents.bucket_domain_name
}

# -----------------------------------------------------------------------------
# ECR Repositories
# -----------------------------------------------------------------------------

output "ecr_repository_urls" {
  description = "Map of ECR repository URLs"
  value = {
    api = aws_ecr_repository.api.repository_url
    web = aws_ecr_repository.web.repository_url
  }
}

output "ecr_api_repository_url" {
  description = "ECR repository URL for the API image"
  value       = aws_ecr_repository.api.repository_url
}

output "ecr_web_repository_url" {
  description = "ECR repository URL for the web image"
  value       = aws_ecr_repository.web.repository_url
}

output "ecr_api_repository_arn" {
  description = "ECR repository ARN for the API image"
  value       = aws_ecr_repository.api.arn
}

output "ecr_web_repository_arn" {
  description = "ECR repository ARN for the web image"
  value       = aws_ecr_repository.web.arn
}
