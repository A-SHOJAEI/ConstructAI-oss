import { apiClient } from "./api-client";

export interface DrawingSet {
  id: string;
  project_id: string;
  name: string;
  discipline: string;
  description: string | null;
  created_at: string;
}

export interface Drawing {
  id: string;
  drawing_set_id: string;
  project_id: string;
  sheet_number: string;
  title: string;
  discipline: string;
  current_revision_id: string | null;
  status: string;
  created_at: string;
}

export interface DrawingRevision {
  id: string;
  drawing_id: string;
  revision_number: number;
  s3_key: string;
  original_filename: string | null;
  file_size_bytes: number | null;
  status: string;
  created_at: string;
}

export interface DrawingSetWithDrawings extends DrawingSet {
  drawings: Drawing[];
}

export const drawingsApi = {
  listSets: (projectId: string) =>
    apiClient.get<{ data: DrawingSet[]; meta: { cursor: string | null; has_more: boolean } }>(
      `/api/v1/projects/${projectId}/drawing-sets`,
    ),

  getSet: (projectId: string, setId: string) =>
    apiClient.get<DrawingSetWithDrawings>(`/api/v1/projects/${projectId}/drawing-sets/${setId}`),

  getDrawing: (projectId: string, drawingId: string) =>
    apiClient.get<Drawing & { revisions: DrawingRevision[] }>(
      `/api/v1/projects/${projectId}/drawings/${drawingId}`,
    ),
};
