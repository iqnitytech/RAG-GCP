output "artifact_registry_repository" {
  value = google_artifact_registry_repository.app.name
}

output "document_bucket" {
  value = google_storage_bucket.documents.name
}

output "runtime_service_account" {
  value = google_service_account.runtime.email
}

output "region" {
  value = var.region
}
