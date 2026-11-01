variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "Primary GCP region"
  type        = string
  default     = "asia-south1"
}

variable "app_name" {
  description = "Application identifier"
  type        = string
  default     = "enterprise-rag"
}
