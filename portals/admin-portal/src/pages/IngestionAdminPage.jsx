import React from "react";
import { ApplicationSelector } from "../components/ApplicationSelector";
import { DocumentLinker } from "../components/DocumentLinker";
import { JobsTrackerTable } from "../components/JobsTrackerTable";
import { SharedFolderIngestionForm } from "../components/SharedFolderIngestionForm";
import { useIngestionAdmin } from "../hooks/useIngestionAdmin";
import { AdminLayout } from "../layouts/AdminLayout";

export function IngestionAdminPage() {
  const {
    apiKey,
    setApiKey,
    applications,
    applicationId,
    setApplicationId,
    jobs,
    documents,
    loading,
    error,
    refresh,
    addApplication,
    triggerSharedFolderIngestion,
    createDocumentLink,
    removeDocumentLink,
  } = useIngestionAdmin("CPQ");

  return (
    <AdminLayout
      apiKey={apiKey}
      onApiKeyChange={setApiKey}
      onRefresh={refresh}
      loading={loading}
    >
      {error && <div className="error-alert">{error}</div>}

      <ApplicationSelector
        applications={applications}
        selectedApplicationId={applicationId}
        onSelectApplication={setApplicationId}
        onAddApplication={addApplication}
      />

      <SharedFolderIngestionForm
        applicationId={applicationId}
        onTriggerIngestion={triggerSharedFolderIngestion}
      />

      <JobsTrackerTable applicationId={applicationId} jobs={jobs} />

      <DocumentLinker
        applicationId={applicationId}
        documents={documents}
        apiKey={apiKey}
        onCreateLink={createDocumentLink}
        onRemoveLink={removeDocumentLink}
      />
    </AdminLayout>
  );
}

export default IngestionAdminPage;
