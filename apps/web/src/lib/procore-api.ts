import { apiClient } from "./api-client";

export interface ProcoreStatus {
  connected: boolean;
  company_name: string | null;
  company_id: number | null;
  connected_at: string | null;
  token_expires_at: string | null;
}

export interface SyncStatus {
  id: string;
  sync_type: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  records_synced: number;
  records_failed: number;
  error_message: string | null;
}

export const procoreApi = {
  getStatus: () => apiClient.get<ProcoreStatus>("/api/v1/integrations/procore/status"),

  getConnectUrl: () =>
    `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/v1/integrations/procore/connect`,

  disconnect: () =>
    apiClient.post<{ message: string }>("/api/v1/integrations/procore/disconnect", {}),

  sync: () =>
    apiClient.post<{ message: string; sync_id: string }>("/api/v1/integrations/procore/sync", {}),

  syncStatus: () => apiClient.get<SyncStatus>("/api/v1/integrations/procore/sync/status"),
};
