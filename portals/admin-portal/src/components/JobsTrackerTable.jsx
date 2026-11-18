import React from "react";

export function JobsTrackerTable({ applicationId, jobs }) {
  const totalProcessed = jobs.reduce((acc, j) => acc + (j.files_processed || 0), 0);
  const totalSucceeded = jobs.reduce((acc, j) => acc + (j.files_succeeded || 0), 0);
  const totalFailed = jobs.reduce((acc, j) => acc + (j.files_failed || 0), 0);

  return (
    <section className="admin-card">
      <div className="admin-card-header">
        <div>
          <h2>Background File-Processing Tracker ({applicationId})</h2>
          <p className="muted">
            Live progress of discovered, processed, succeeded, and failed files across background ingestion jobs.
          </p>
        </div>
        <div className="kpi-badges">
          <span className="kpi-badge">Processed: {totalProcessed}</span>
          <span className="kpi-badge success">Succeeded: {totalSucceeded}</span>
          <span className="kpi-badge danger">Failed: {totalFailed}</span>
        </div>
      </div>

      {jobs.length === 0 ? (
        <p className="empty-state">No ingestion jobs recorded yet for application "{applicationId}".</p>
      ) : (
        <div className="table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Job ID</th>
                <th>App ID</th>
                <th>Source</th>
                <th>Status</th>
                <th>Discovered</th>
                <th>Processed</th>
                <th>Succeeded</th>
                <th>Failed</th>
                <th>Chunks</th>
                <th>Processed Files</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.job_id}>
                  <td>
                    <code>{job.job_id.slice(0, 10)}</code>
                  </td>
                  <td>
                    <span className="tag">{job.application_id}</span>
                  </td>
                  <td>{job.source_type}</td>
                  <td>
                    <span className={`status-pill status-${job.status}`}>{job.status}</span>
                  </td>
                  <td>{job.files_discovered}</td>
                  <td>
                    <strong>
                      {job.files_processed} / {job.files_discovered}
                    </strong>
                  </td>
                  <td className="text-success">{job.files_succeeded}</td>
                  <td className="text-danger">{job.files_failed}</td>
                  <td>{job.chunks_indexed}</td>
                  <td>
                    {(job.processed_files || []).slice(0, 4).join(", ")}
                    {(job.processed_files || []).length > 4 &&
                      ` (+${job.processed_files.length - 4} more)`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
