locals {
  required_apis = [
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "storage.googleapis.com",
    "aiplatform.googleapis.com",
    "secretmanager.googleapis.com",
    "pubsub.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com"
  ]

  labels = {
    application = var.app_name
    environment = "development"
    managed_by  = "terraform"
  }
}

# Enable required APIs
resource "google_project_service" "apis" {
  for_each = toset(local.required_apis)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# Docker image repository
resource "google_artifact_registry_repository" "app" {
  project       = var.project_id
  location      = var.region
  repository_id = "${var.app_name}-images"
  description   = "Docker images for Enterprise RAG"
  format        = "DOCKER"
  labels        = local.labels

  depends_on = [
    google_project_service.apis
  ]
}

# Document storage bucket
resource "google_storage_bucket" "documents" {
  project                     = var.project_id
  name                        = "${var.project_id}-${var.app_name}-documents"
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  labels = local.labels

  depends_on = [
    google_project_service.apis
  ]
}

# Cloud Run runtime identity
resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "${var.app_name}-runtime"
  display_name = "Enterprise RAG Cloud Run Runtime"
}

# Vertex AI permission
resource "google_project_iam_member" "vertex_access" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# Document bucket permission
resource "google_storage_bucket_iam_member" "storage_access" {
  bucket = google_storage_bucket.documents.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

# Secret Manager permission
resource "google_project_iam_member" "secret_access" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}
