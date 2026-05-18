"use client";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import type { Camera, CameraCreate } from "@/lib/safety-api";
import { safetyApi } from "@/lib/safety-api";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";

export default function CamerasPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formData, setFormData] = useState<CameraCreate>({
    project_id: projectId ?? "",
    name: "",
    stream_url: "",
    location_description: "",
    fps_setting: 15,
  });

  const loadCameras = async () => {
    if (!projectId) return;
    try {
      const response = await safetyApi.listCameras(projectId);
      setCameras(response.data);
    } catch {
      toast.error("Failed to load cameras. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadCameras();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- loadCameras recreated each render; projectId is the real dep
  }, [projectId]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await safetyApi.createCamera({ ...formData, project_id: projectId ?? "" });
      setShowForm(false);
      setFormData({ project_id: projectId ?? "", name: "", stream_url: "", fps_setting: 15 });
      await loadCameras();
    } catch {
      toast.error("Failed to create camera. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this camera?")) return;
    try {
      await safetyApi.deleteCamera(id);
      await loadCameras();
    } catch {
      toast.error("Failed to delete camera. Please try again.");
    }
  };

  if (!projectId) return <NoProjectSelected />;

  if (loading) {
    return <div className="p-6">Loading cameras...</div>;
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Camera Management</h1>
        <button
          onClick={() => setShowForm(!showForm)}
          className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700"
        >
          {showForm ? "Cancel" : "Add Camera"}
        </button>
      </div>

      {showForm && (
        <form
          onSubmit={handleSubmit}
          className="bg-white dark:bg-gray-800 rounded-lg shadow-sm p-6 mb-6"
        >
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Name
              </label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-200 rounded-md text-sm"
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Stream URL
              </label>
              <input
                type="text"
                value={formData.stream_url}
                onChange={(e) => setFormData({ ...formData, stream_url: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-200 rounded-md text-sm"
                placeholder="rtsp://..."
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Location
              </label>
              <input
                type="text"
                value={formData.location_description || ""}
                onChange={(e) => setFormData({ ...formData, location_description: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-200 rounded-md text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                FPS
              </label>
              <input
                type="number"
                value={formData.fps_setting}
                onChange={(e) =>
                  setFormData({ ...formData, fps_setting: parseInt(e.target.value) || 15 })
                }
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-200 rounded-md text-sm"
                min={1}
                max={30}
              />
            </div>
          </div>
          <button
            type="submit"
            disabled={submitting}
            className="mt-4 px-4 py-2 bg-green-600 text-white text-sm font-medium rounded-md hover:bg-green-700 disabled:opacity-50"
          >
            Create Camera
          </button>
        </form>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {cameras.map((camera) => (
          <div key={camera.id} className="bg-white dark:bg-gray-800 rounded-lg shadow-sm p-4">
            <div className="flex items-center justify-between mb-2">
              <h3 className="font-medium text-gray-900 dark:text-white">{camera.name}</h3>
              <span
                className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                  camera.status === "active"
                    ? "bg-green-100 text-green-800"
                    : "bg-gray-100 text-gray-600"
                }`}
              >
                {camera.status}
              </span>
            </div>
            <p className="text-sm text-gray-500 dark:text-gray-400 truncate">{camera.stream_url}</p>
            {camera.location_description && (
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                {camera.location_description}
              </p>
            )}
            <div className="flex items-center justify-between mt-3 pt-3 border-t border-gray-100 dark:border-gray-700">
              <span className="text-xs text-gray-400 dark:text-gray-500">
                {camera.fps_setting} FPS
              </span>
              <button
                onClick={() => handleDelete(camera.id)}
                className="text-red-600 hover:text-red-800 text-sm"
              >
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>

      {cameras.length === 0 && !showForm && (
        <div className="text-center py-12 text-gray-500 dark:text-gray-400">
          <p>No cameras configured. Click &quot;Add Camera&quot; to get started.</p>
        </div>
      )}
    </div>
  );
}
