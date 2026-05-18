"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { apiClient } from "@/lib/api-client";
import {
  ArrowLeft,
  MapPin,
  Calendar,
  Camera,
  FileText,
  Building2,
  AlertTriangle,
} from "lucide-react";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";

interface PunchListDetail {
  id: string;
  project_id: string;
  item_number: string;
  description: string;
  location: string | null;
  category: string | null;
  priority: string;
  status: string;
  assigned_to: string | null;
  created_by: string | null;
  due_date: string | null;
  completed_date: string | null;
  photos: { file_name?: string; caption?: string; gps_lat?: number; gps_lon?: number }[];
  notes: string | null;
  gps_lat: number | null;
  gps_lon: number | null;
  drawing_reference: string | null;
  company: string | null;
  created_at: string;
  updated_at: string;
}

const statusColors: Record<string, string> = {
  open: "bg-red-100 text-red-800",
  in_progress: "bg-yellow-100 text-yellow-800",
  resolved: "bg-blue-100 text-blue-800",
  verified: "bg-green-100 text-green-800",
};

const priorityColors: Record<string, string> = {
  critical: "bg-red-100 text-red-800",
  high: "bg-orange-100 text-orange-800",
  medium: "bg-blue-100 text-blue-800",
  low: "bg-gray-100 text-gray-600",
};

const statusOptions = ["open", "in_progress", "resolved", "verified"];

export default function PunchListDetailPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const params = useParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const itemId = params.itemId as string;

  const [updatingStatus, setUpdatingStatus] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const {
    data: item,
    isLoading,
    error: loadError,
  } = useQuery<PunchListDetail>({
    queryKey: ["punch-list-detail", projectId, itemId],
    queryFn: () =>
      apiClient.get<PunchListDetail>(`/api/v1/projects/${projectId}/punch-list/${itemId}`),
    enabled: !!itemId && !!projectId,
  });

  if (!projectId) return <NoProjectSelected />;

  const handleStatusChange = async (newStatus: string) => {
    setUpdatingStatus(true);
    setError(null);
    try {
      await apiClient.patch(`/api/v1/projects/${projectId}/punch-list/${itemId}`, {
        status: newStatus,
      });
      queryClient.invalidateQueries({ queryKey: ["punch-list-detail", itemId] });
      queryClient.invalidateQueries({ queryKey: ["punch-list"] });
      queryClient.invalidateQueries({ queryKey: ["punch-list-stats"] });
    } catch {
      setError("Failed to update status.");
    } finally {
      setUpdatingStatus(false);
    }
  };

  if (isLoading) {
    return (
      <div className="p-6">
        <div className="text-center text-gray-500 py-12">Loading punch list item...</div>
      </div>
    );
  }

  if (loadError || !item) {
    return (
      <div className="p-6">
        <div className="p-4 text-red-800 bg-red-50 rounded">Failed to load punch list item</div>
      </div>
    );
  }

  const isOverdue =
    item.due_date &&
    new Date(item.due_date) < new Date() &&
    (item.status === "open" || item.status === "in_progress");

  return (
    <div className="p-4 md:p-6 max-w-4xl mx-auto">
      {/* Back */}
      <button
        onClick={() => router.back()}
        className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-4"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to Punch List
      </button>

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between mb-6 gap-3">
        <div>
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-2xl font-bold text-gray-900">{item.item_number}</h1>
            <span
              className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                statusColors[item.status] ?? "bg-gray-100 text-gray-800"
              }`}
            >
              {item.status.replace(/_/g, " ")}
            </span>
            <span
              className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                priorityColors[item.priority] ?? "bg-gray-100 text-gray-800"
              }`}
            >
              {item.priority}
            </span>
            {isOverdue && (
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">
                <AlertTriangle className="h-3 w-3" />
                Overdue
              </span>
            )}
          </div>
          <p className="text-gray-700 mt-2">{item.description}</p>
        </div>

        {/* Status update buttons */}
        <div className="flex gap-2 flex-wrap">
          {statusOptions
            .filter((s) => s !== item.status)
            .map((s) => (
              <button
                key={s}
                onClick={() => handleStatusChange(s)}
                disabled={updatingStatus}
                className={`px-3 py-1.5 text-xs font-medium rounded-lg border disabled:opacity-50 ${
                  statusColors[s] ?? "bg-gray-100"
                }`}
              >
                {s.replace(/_/g, " ")}
              </button>
            ))}
        </div>
      </div>

      {error && <div className="mb-4 p-3 text-sm text-red-800 bg-red-50 rounded">{error}</div>}

      {/* Details Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-3">Details</h3>
          <dl className="space-y-2 text-sm">
            {item.location && (
              <div className="flex justify-between">
                <dt className="text-gray-500 flex items-center gap-1">
                  <MapPin className="h-3 w-3" />
                  Location
                </dt>
                <dd className="text-gray-900">{item.location}</dd>
              </div>
            )}
            {item.category && (
              <div className="flex justify-between">
                <dt className="text-gray-500">Category</dt>
                <dd className="text-gray-900 capitalize">{item.category}</dd>
              </div>
            )}
            {item.company && (
              <div className="flex justify-between">
                <dt className="text-gray-500 flex items-center gap-1">
                  <Building2 className="h-3 w-3" />
                  Company
                </dt>
                <dd className="text-gray-900">{item.company}</dd>
              </div>
            )}
            {item.drawing_reference && (
              <div className="flex justify-between">
                <dt className="text-gray-500 flex items-center gap-1">
                  <FileText className="h-3 w-3" />
                  Drawing
                </dt>
                <dd className="text-gray-900">{item.drawing_reference}</dd>
              </div>
            )}
            {item.due_date && (
              <div className="flex justify-between">
                <dt className="text-gray-500 flex items-center gap-1">
                  <Calendar className="h-3 w-3" />
                  Due Date
                </dt>
                <dd className="text-gray-900">{new Date(item.due_date).toLocaleDateString()}</dd>
              </div>
            )}
            {item.completed_date && (
              <div className="flex justify-between">
                <dt className="text-gray-500">Completed</dt>
                <dd className="text-gray-900">
                  {new Date(item.completed_date).toLocaleDateString()}
                </dd>
              </div>
            )}
          </dl>
        </div>

        {/* GPS / Map placeholder */}
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-3 flex items-center gap-2">
            <MapPin className="h-4 w-4" />
            Location
          </h3>
          {item.gps_lat && item.gps_lon ? (
            <div>
              <div className="bg-gray-100 rounded aspect-video flex items-center justify-center mb-2">
                <div className="text-center">
                  <MapPin className="h-8 w-8 text-blue-500 mx-auto" />
                  <p className="text-xs text-gray-500 mt-1">
                    {item.gps_lat.toFixed(6)}, {item.gps_lon.toFixed(6)}
                  </p>
                </div>
              </div>
            </div>
          ) : (
            <p className="text-sm text-gray-400">No GPS coordinates recorded.</p>
          )}
        </div>
      </div>

      {/* Photos */}
      {item.photos.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-4 mb-6">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-3 flex items-center gap-2">
            <Camera className="h-4 w-4" />
            Photos ({item.photos.length})
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            {item.photos.map((photo, i) => (
              <div
                key={i}
                className="bg-gray-100 rounded aspect-square flex items-center justify-center"
              >
                <div className="text-center">
                  <Camera className="h-6 w-6 text-gray-400 mx-auto" />
                  <p className="text-xs text-gray-500 mt-1">
                    {photo.caption || photo.file_name || `Photo ${i + 1}`}
                  </p>
                  {photo.gps_lat && photo.gps_lon && (
                    <p className="text-xs text-gray-400">
                      {photo.gps_lat.toFixed(4)}, {photo.gps_lon.toFixed(4)}
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Notes */}
      {item.notes && (
        <div className="bg-white rounded-lg border border-gray-200 p-4 mb-6">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-2">Notes</h3>
          <p className="text-gray-900 whitespace-pre-wrap">{item.notes}</p>
        </div>
      )}

      {/* Metadata */}
      <div className="text-xs text-gray-400">
        Created {new Date(item.created_at).toLocaleString()}
        {" | "}
        Updated {new Date(item.updated_at).toLocaleString()}
      </div>
    </div>
  );
}
