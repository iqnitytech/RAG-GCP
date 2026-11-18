import React, { useState } from "react";
import { ingestionApi } from "../services/ingestionApi";

const RELATION_TYPES = [
  { value: "depends_on", label: "Depends On (Target must be retrieved with Source)" },
  { value: "related", label: "Related To (Symmetric link)" },
  { value: "attachment_of", label: "Attachment Of" },
  { value: "amends", label: "Amends (Amendment to master document)" },
  { value: "supersedes", label: "Supersedes (Newer version of document)" },
  { value: "references", label: "References" },
];

export function DocumentLinker({
  applicationId,
  documents,
  apiKey,
  onCreateLink,
  onRemoveLink,
}) {
  const [sourceDocId, setSourceDocId] = useState("");
  const [targetDocId, setTargetDocId] = useState("");
  const [relationType, setRelationType] = useState("depends_on");
  const [createdBy, setCreatedBy] = useState("admin@portal");
  const [selectedOverview, setSelectedOverview] = useState(null);
  const [feedback, setFeedback] = useState("");

  const handleLink = async (e) => {
    e.preventDefault();
    if (!sourceDocId || !targetDocId) return;
    setFeedback("");
    try {
      await onCreateLink({
        sourceDocumentId: sourceDocId,
        targetDocumentId: targetDocId,
        relationType,
        createdBy,
      });
      setFeedback(`Linked ${sourceDocId} -> ${targetDocId} (${relationType})`);
      if (selectedOverview && selectedOverview.document_id === sourceDocId) {
        const fresh = await ingestionApi.getDocumentLinks(sourceDocId, apiKey);
        setSelectedOverview(fresh);
      }
    } catch (err) {
      setFeedback(`Error: ${err.message}`);
    }
  };

  const inspectDocLinks = async (docId) => {
    try {
      const overview = await ingestionApi.getDocumentLinks(docId, apiKey);
      setSelectedOverview(overview);
    } catch (err) {
      setFeedback(`Error loading links: ${err.message}`);
    }
  };

  const handleUnlink = async (docId, linkId) => {
    await onRemoveLink({ documentId: docId, linkId });
    const fresh = await ingestionApi.getDocumentLinks(docId, apiKey);
    setSelectedOverview(fresh);
  };

  return (
    <section className="admin-card">
      <h2>Document Dependency Mapping ({applicationId})</h2>
      <p className="muted">
        Link uploaded documents within <strong>{applicationId}</strong> so dependent documents are retrieved
        together during RAG queries. Circular dependencies on directed relations are automatically blocked.
      </p>

      <form className="grid-form" onSubmit={handleLink}>
        <label>
          Source Document
          <select value={sourceDocId} onChange={(e) => setSourceDocId(e.target.value)} required>
            <option value="">-- Select Source Document --</option>
            {documents.map((d) => (
              <option key={d.document_id} value={d.document_id}>
                {d.filename} ({d.document_id})
              </option>
            ))}
          </select>
        </label>

        <label>
          Relationship Type
          <select value={relationType} onChange={(e) => setRelationType(e.target.value)}>
            {RELATION_TYPES.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </label>

        <label>
          Target (Dependent) Document
          <select value={targetDocId} onChange={(e) => setTargetDocId(e.target.value)} required>
            <option value="">-- Select Target Document --</option>
            {documents.map((d) => (
              <option key={d.document_id} value={d.document_id}>
                {d.filename} ({d.document_id})
              </option>
            ))}
          </select>
        </label>

        <label>
          Created By (Admin)
          <input type="text" value={createdBy} onChange={(e) => setCreatedBy(e.target.value)} />
        </label>

        <div className="form-actions full-width">
          <button type="submit" className="btn-primary">
            Link Documents
          </button>
          {feedback && <span className="status-banner">{feedback}</span>}
        </div>
      </form>

      <h3>Indexed Documents in {applicationId}</h3>
      {documents.length === 0 ? (
        <p className="empty-state">No documents indexed yet for "{applicationId}".</p>
      ) : (
        <div className="table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Document ID</th>
                <th>App ID</th>
                <th>Filename</th>
                <th>Source URI</th>
                <th>Chunks</th>
                <th>Linked Documents</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => (
                <tr key={doc.document_id}>
                  <td>
                    <code>{doc.document_id}</code>
                  </td>
                  <td>
                    <span className="tag">{doc.application_id}</span>
                  </td>
                  <td>{doc.filename}</td>
                  <td>
                    <code>{doc.source}</code>
                  </td>
                  <td>{doc.chunk_count}</td>
                  <td>
                    {(doc.linked_document_ids || []).length === 0
                      ? "None"
                      : doc.linked_document_ids.join(", ")}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => inspectDocLinks(doc.document_id)}
                    >
                      Manage Links
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selectedOverview && (
        <div className="links-drawer">
          <h4>Links for Document `{selectedOverview.document_id}`</h4>
          <ul>
            {selectedOverview.outgoing.map((l) => (
              <li key={l.link_id}>
                <code>{l.source_document_id}</code> &mdash;<strong>[{l.relation_type}]</strong>&rarr;{" "}
                <code>{l.target_document_id}</code>{" "}
                <button
                  type="button"
                  className="btn-danger-sm"
                  onClick={() => handleUnlink(selectedOverview.document_id, l.link_id)}
                >
                  Unlink
                </button>
              </li>
            ))}
            {selectedOverview.incoming.map((l) => (
              <li key={l.link_id}>
                <code>{l.source_document_id}</code> &mdash;<strong>[{l.relation_type}]</strong>&rarr;{" "}
                <code>{l.target_document_id}</code> (incoming){" "}
                <button
                  type="button"
                  className="btn-danger-sm"
                  onClick={() => handleUnlink(selectedOverview.document_id, l.link_id)}
                >
                  Unlink
                </button>
              </li>
            ))}
            {selectedOverview.outgoing.length === 0 && selectedOverview.incoming.length === 0 && (
              <li>No active links for this document.</li>
            )}
          </ul>
        </div>
      )}
    </section>
  );
}
