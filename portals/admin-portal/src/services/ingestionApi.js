/**
 * API client for the Enterprise RAG Admin Portal.
 * Connects to /ingestion endpoints with multi-application (application_id) support.
 */

const DEFAULT_BASE_URL =
  (typeof import.meta !== "undefined" && import.meta.env && import.meta.env.VITE_API_BASE_URL) ||
  "http://127.0.0.1:8000";

async function requestJson(path, { method = "GET", body, apiKey = "" } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (apiKey) {
    headers["X-API-Key"] = apiKey;
  }
  const response = await fetch(`${DEFAULT_BASE_URL}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `Request failed with HTTP ${response.status}`);
  }
  return data;
}

export const ingestionApi = {
  listApplications(apiKey) {
    return requestJson("/ingestion/applications", { apiKey });
  },

  registerApplication({ applicationId, name, description }, apiKey) {
    return requestJson("/ingestion/applications", {
      method: "POST",
      apiKey,
      body: {
        application_id: applicationId,
        name: name || applicationId,
        description: description || "",
      },
    });
  },

  startSharedPathJob({ applicationId, provider, sharedPaths, zone = "raw", limit = 200 }, apiKey) {
    return requestJson("/ingestion/shared-paths", {
      method: "POST",
      apiKey,
      body: {
        application_id: applicationId,
        provider,
        shared_paths: sharedPaths,
        zone,
        limit,
      },
    });
  },

  listJobs(applicationId, apiKey) {
    const query = applicationId ? `?application_id=${encodeURIComponent(applicationId)}` : "";
    return requestJson(`/ingestion/jobs${query}`, { apiKey });
  },

  getJob(jobId, apiKey) {
    return requestJson(`/ingestion/jobs/${encodeURIComponent(jobId)}`, { apiKey });
  },

  listDocuments(applicationId, apiKey) {
    const query = applicationId ? `?application_id=${encodeURIComponent(applicationId)}` : "";
    return requestJson(`/ingestion/documents${query}`, { apiKey });
  },

  getDocumentLinks(documentId, apiKey) {
    return requestJson(`/ingestion/documents/${encodeURIComponent(documentId)}/links`, { apiKey });
  },

  linkDocuments({ sourceDocumentId, targetDocumentId, relationType = "depends_on", createdBy = "" }, apiKey) {
    return requestJson(`/ingestion/documents/${encodeURIComponent(sourceDocumentId)}/links`, {
      method: "POST",
      apiKey,
      body: {
        target_document_id: targetDocumentId,
        relation_type: relationType,
        created_by: createdBy,
      },
    });
  },

  unlinkDocuments({ documentId, linkId }, apiKey) {
    return requestJson(
      `/ingestion/documents/${encodeURIComponent(documentId)}/links/${encodeURIComponent(linkId)}`,
      { method: "DELETE", apiKey }
    );
  },
};

export default ingestionApi;
