import { apiClient } from "./api-client";

export interface Camera {
  id: string;
  project_id: string;
  name: string;
  stream_url: string;
  location_description: string | null;
  fps_setting: number;
  resolution: string | null;
  status: string;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface CameraCreate {
  project_id: string;
  name: string;
  stream_url: string;
  location_description?: string;
  fps_setting?: number;
  resolution?: string;
  config?: Record<string, unknown>;
}

export interface SafetyZone {
  id: string;
  camera_id: string;
  name: string;
  zone_type:
    | "restricted"
    | "crane_swing"
    | "excavation"
    | "ppe_required"
    | "equipment_only"
    | "pedestrian_only"
    | "general";
  polygon_points: number[][];
  ppe_requirements: string[];
  severity_override: string | null;
  is_active: boolean;
  schedule: Record<string, unknown> | null;
  created_at: string;
}

export interface AlertDetection {
  class_name: string;
  confidence: number;
  bbox: number[];
  track_id?: number | null;
  violation_type?: string | null;
}

export interface SafetyAlert {
  id: string;
  project_id: string;
  camera_id: string | null;
  zone_id: string | null;
  alert_type: string;
  priority: "P1_critical" | "P2_high" | "P3_medium" | "P4_low" | "P5_info";
  description: string;
  confidence: number;
  detections: AlertDetection[];
  frame_s3_key: string | null;
  video_clip_s3_key: string | null;
  osha_reference: string | null;
  is_acknowledged: boolean;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  is_false_positive: boolean | null;
  response_notes: string | null;
  created_at: string;
}

export interface SafetyStats {
  total_alerts: number;
  alerts_by_priority: Record<string, number>;
  alerts_by_type: Record<string, number>;
  acknowledged_count: number;
  false_positive_count: number;
  period: string;
}

export interface DetectionEvent {
  camera_id: string;
  zone_id: string | null;
  class_name: string;
  confidence: number;
  bbox: number[];
  track_id: string | null;
  violation_type: string | null;
  timestamp: string;
}

function buildQueryString(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(
    (entry): entry is [string, string | number] => entry[1] !== undefined,
  );
  if (entries.length === 0) return "";
  return (
    "?" + entries.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&")
  );
}

export const safetyApi = {
  // Camera endpoints
  listCameras: (projectId: string) =>
    apiClient.get<{ data: Camera[] }>(`/api/v1/cameras?project_id=${projectId}`),

  createCamera: (data: CameraCreate) => apiClient.post<Camera>("/api/v1/cameras", data),

  getCamera: (id: string) => apiClient.get<Camera>(`/api/v1/cameras/${id}`),

  updateCamera: (id: string, data: Partial<CameraCreate>) =>
    apiClient.patch<Camera>(`/api/v1/cameras/${id}`, data),

  deleteCamera: (id: string) => apiClient.delete(`/api/v1/cameras/${id}`),

  // Zone endpoints
  listZones: (cameraId: string) =>
    apiClient.get<{ data: SafetyZone[] }>(`/api/v1/zones?camera_id=${cameraId}`),

  createZone: (data: Omit<SafetyZone, "id" | "created_at">) =>
    apiClient.post<SafetyZone>("/api/v1/zones", data),

  updateZone: (id: string, data: Partial<SafetyZone>) =>
    apiClient.patch<SafetyZone>(`/api/v1/zones/${id}`, data),

  deleteZone: (id: string) => apiClient.delete(`/api/v1/zones/${id}`),

  // Alert endpoints
  listAlerts: (params: {
    project_id?: string;
    priority?: string;
    alert_type?: string;
    limit?: number;
  }) =>
    apiClient.get<{ data: SafetyAlert[]; total: number }>(
      `/api/v1/safety/alerts${buildQueryString(params)}`,
    ),

  getAlert: (id: string) => apiClient.get<SafetyAlert>(`/api/v1/safety/alerts/${id}`),

  acknowledgeAlert: (id: string, data: { is_false_positive?: boolean; notes?: string }) =>
    apiClient.patch<SafetyAlert>(`/api/v1/safety/alerts/${id}/acknowledge`, data),

  // Stats
  getStats: (projectId: string) =>
    apiClient.get<SafetyStats>(`/api/v1/safety/stats?project_id=${projectId}`),
};
