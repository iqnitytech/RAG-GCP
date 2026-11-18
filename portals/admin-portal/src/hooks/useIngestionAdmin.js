import { useCallback, useEffect, useState } from "react";
import { ingestionApi } from "../services/ingestionApi";

export function useIngestionAdmin(initialAppId = "CPQ") {
  const [apiKey, setApiKey] = useState("");
  const [applications, setApplications] = useState([
    { application_id: "CPQ", name: "Configure, Price, Quote", description: "Pricing & quote documents" },
    { application_id: "CustomerExperience", name: "Customer Experience", description: "Support & CX knowledge" },
  ]);
  const [applicationId, setApplicationId] = useState(initialAppId);
  const [jobs, setJobs] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [apps, jobList, docList] = await Promise.all([
        ingestionApi.listApplications(apiKey),
        ingestionApi.listJobs(applicationId, apiKey),
        ingestionApi.listDocuments(applicationId, apiKey),
      ]);
      if (Array.isArray(apps) && apps.length > 0) {
        setApplications(apps);
      }
      setJobs(jobList);
      setDocuments(docList);
    } catch (err) {
      setError(err.message || "Failed to load Admin Portal data");
    } finally {
      setLoading(false);
    }
  }, [applicationId, apiKey]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 4000);
    return () => clearInterval(timer);
  }, [refresh]);

  const addApplication = async (newAppId, name, description) => {
    const created = await ingestionApi.registerApplication(
      { applicationId: newAppId, name, description },
      apiKey
    );
    setApplicationId(created.application_id);
    await refresh();
    return created;
  };

  const triggerSharedFolderIngestion = async ({ provider, sharedPaths, zone, limit }) => {
    const job = await ingestionApi.startSharedPathJob(
      { applicationId, provider, sharedPaths, zone, limit },
      apiKey
    );
    await refresh();
    return job;
  };

  const createDocumentLink = async ({ sourceDocumentId, targetDocumentId, relationType, createdBy }) => {
    const link = await ingestionApi.linkDocuments(
      { sourceDocumentId, targetDocumentId, relationType, createdBy },
      apiKey
    );
    await refresh();
    return link;
  };

  const removeDocumentLink = async ({ documentId, linkId }) => {
    await ingestionApi.unlinkDocuments({ documentId, linkId }, apiKey);
    await refresh();
  };

  return {
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
  };
}
