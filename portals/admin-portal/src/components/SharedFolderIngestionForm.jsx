import React, { useState } from "react";

const PROVIDERS = [
  { value: "filesystem", label: "Shared Folder / Filesystem (Local or Mounted Network Share)" },
  { value: "gcs", label: "Google Cloud Storage (GCS Bucket Folder Prefix)" },
  { value: "data_lake", label: "EDH Data Lake (Medallion Zone Folder)" },
  { value: "salesforce", label: "Salesforce Shared Paths (ContentVersion)" },
  { value: "databricks", label: "Databricks Unity Catalog Volumes Folder" },
  { value: "object_storage_hub", label: "Object Storage Hub (S3 / MinIO / GCS XML)" },
  { value: "api_hub", label: "API-Driven Data Hub Folder" },
];

const ZONES = ["raw", "curated", "gold", "landing", "quarantine"];

export function SharedFolderIngestionForm({ applicationId, onTriggerIngestion }) {
  const [provider, setProvider] = useState("filesystem");
  const [pathsText, setPathsText] = useState("shared/policies\nshared/contracts");
  const [zone, setZone] = useState("raw");
  const [limit, setLimit] = useState(200);
  const [submitting, setSubmitting] = useState(false);
  const [statusMsg, setStatusMsg] = useState("");

  const handleSubmit = async (e) => {
    e.preventDefault();
    const sharedPaths = pathsText
      .split(/[\n,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (sharedPaths.length === 0) return;

    setSubmitting(true);
    setStatusMsg("");
    try {
      const job = await onTriggerIngestion({
        provider,
        sharedPaths,
        zone,
        limit: Number(limit) || 200,
      });
      setStatusMsg(
        `Queued background job ${job.job_id} for application "${applicationId}" (${provider})`
      );
    } catch (err) {
      setStatusMsg(`Error: ${err.message}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="admin-card">
      <h2>Background Shared Folder Ingestion ({applicationId})</h2>
      <p className="muted">
        Scan any shared folder path in the background, parse &amp; chunk all supported files (PDF, DOCX, XLSX,
        PPTX, CSV, TXT, HTML, JSON, Images), and track how many files are processed.
      </p>

      <form className="grid-form" onSubmit={handleSubmit}>
        <label>
          Folder Provider
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            {PROVIDERS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>

        <label>
          Data Zone (for EDH strategies)
          <select value={zone} onChange={(e) => setZone(e.target.value)}>
            {ZONES.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </select>
        </label>

        <label>
          Max Files Limit
          <input
            type="number"
            min="1"
            max="2000"
            value={limit}
            onChange={(e) => setLimit(e.target.value)}
          />
        </label>

        <label className="full-width">
          Shared Folder Paths (one path or prefix per line)
          <textarea
            rows={3}
            value={pathsText}
            onChange={(e) => setPathsText(e.target.value)}
            placeholder="e.g. cpq/pricing-guides&#10;cpq/discount-matrix"
            required
          />
        </label>

        <div className="form-actions full-width">
          <button type="submit" className="btn-primary" disabled={submitting}>
            {submitting ? "Starting Background Job..." : `Start Background Ingestion for ${applicationId}`}
          </button>
          {statusMsg && <span className="status-banner">{statusMsg}</span>}
        </div>
      </form>
    </section>
  );
}
