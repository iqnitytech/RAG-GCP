import React from "react";

export function AdminLayout({ apiKey, onApiKeyChange, onRefresh, loading, children }) {
  return (
    <div className="admin-shell">
      <header className="admin-topbar">
        <div className="admin-brand">
          <span className="admin-logo">RAG-GCP</span>
          <div>
            <h1>Enterprise RAG Admin Portal</h1>
            <p className="subtitle">
              Multi-Application Shared Folder Ingestion, File Progress Tracker &amp; Document Dependency Mapping
            </p>
          </div>
        </div>
        <div className="admin-topbar-controls">
          <input
            type="password"
            placeholder="X-API-Key (optional in dev)"
            value={apiKey}
            onChange={(e) => onApiKeyChange(e.target.value)}
          />
          <button type="button" className="btn-secondary" onClick={onRefresh} disabled={loading}>
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </header>
      <main className="admin-content">{children}</main>
    </div>
  );
}
