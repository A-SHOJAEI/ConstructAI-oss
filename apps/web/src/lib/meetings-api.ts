import { apiClient } from "./api-client";

export interface MeetingMinutes {
  id: string;
  project_id: string;
  meeting_type: string;
  meeting_date: string;
  title: string;
  attendees: Record<string, unknown>[];
  meeting_location: string | null;
  start_time: string | null;
  end_time: string | null;
  agenda_items: Record<string, unknown>[];
  action_items: Record<string, unknown>[];
  decisions: Record<string, unknown>[];
  notes: string | null;
  summary: string | null;
  status: string;
  created_at: string;
}

export const meetingsApi = {
  list: (projectId: string) =>
    apiClient.get<{ data: MeetingMinutes[]; meta: { cursor: string | null; has_more: boolean } }>(
      `/api/v1/communication/meetings?project_id=${projectId}&limit=100`,
    ),

  get: (id: string) => apiClient.get<MeetingMinutes>(`/api/v1/communication/meetings/${id}`),

  create: (data: Record<string, unknown>) =>
    apiClient.post<MeetingMinutes>("/api/v1/communication/meetings", data),

  update: (id: string, data: Record<string, unknown>) =>
    apiClient.patch<MeetingMinutes>(`/api/v1/communication/meetings/${id}`, data),
};
