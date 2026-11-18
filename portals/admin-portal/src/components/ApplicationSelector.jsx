import React, { useState } from "react";

export function ApplicationSelector({
  applications,
  selectedApplicationId,
  onSelectApplication,
  onAddApplication,
}) {
  const [newAppId, setNewAppId] = useState("");
  const [newAppName, setNewAppName] = useState("");
  const [showAdd, setShowAdd] = useState(false);

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!newAppId.trim()) return;
    await onAddApplication(newAppId.trim(), newAppName.trim() || newAppId.trim(), "");
    setNewAppId("");
    setNewAppName("");
    setShowAdd(false);
  };

  return (
    <section className="admin-card">
      <div className="admin-card-header">
        <div>
          <h2>Application Scope (`application_id`)</h2>
          <p className="muted">
            Isolate document ingestion, background folder scans, and document dependency links per application
            (e.g. <strong>1. CPQ</strong>, <strong>2. CustomerExperience</strong>).
          </p>
        </div>
        <button type="button" className="btn-secondary" onClick={() => setShowAdd(!showAdd)}>
          {showAdd ? "Cancel" : "+ Register Application"}
        </button>
      </div>

      <div className="app-pill-bar">
        {applications.map((app) => (
          <button
            key={app.application_id}
            type="button"
            className={`app-pill ${selectedApplicationId === app.application_id ? "active" : ""}`}
            onClick={() => onSelectApplication(app.application_id)}
          >
            <span className="app-pill-id">{app.application_id}</span>
            {app.name && app.name !== app.application_id && (
              <span className="app-pill-name">{app.name}</span>
            )}
          </button>
        ))}
      </div>

      {showAdd && (
        <form className="inline-form" onSubmit={handleCreate}>
          <input
            type="text"
            placeholder="Application ID (e.g. BillingHub)"
            value={newAppId}
            onChange={(e) => setNewAppId(e.target.value)}
            required
          />
          <input
            type="text"
            placeholder="Display Name (optional)"
            value={newAppName}
            onChange={(e) => setNewAppName(e.target.value)}
          />
          <button type="submit" className="btn-primary">
            Save Application
          </button>
        </form>
      )}
    </section>
  );
}
