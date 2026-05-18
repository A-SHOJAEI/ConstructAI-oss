"use client";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { CameraFeed } from "./camera-feed";
import type { Camera } from "@/lib/safety-api";
import { safetyApi } from "@/lib/safety-api";

interface CameraGridProps {
  projectId: string;
}

export function CameraGrid({ projectId }: CameraGridProps) {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedCamera, setSelectedCamera] = useState<string | null>(null);

  useEffect(() => {
    // L-16: cancel the in-flight fetch on unmount or projectId change so we
    // don't setState after unmount (React warns) or apply stale data for
    // the previous project.
    let cancelled = false;
    const loadCameras = async () => {
      try {
        const response = await safetyApi.listCameras(projectId);
        if (!cancelled) setCameras(response.data);
      } catch {
        if (!cancelled) toast.error("Failed to load cameras.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    loadCameras();
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  if (loading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {[1, 2, 3].map((i) => (
          <div key={i} className="aspect-video bg-gray-800 rounded-lg animate-pulse" />
        ))}
      </div>
    );
  }

  if (cameras.length === 0) {
    return (
      <div className="text-center py-12 text-gray-500">
        <p className="text-lg">No cameras configured</p>
        <p className="text-sm mt-2">Add cameras to start monitoring</p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {cameras.map((camera) => (
        <div
          key={camera.id}
          className={`cursor-pointer rounded-lg border-2 transition-colors ${
            selectedCamera === camera.id
              ? "border-blue-500"
              : "border-transparent hover:border-gray-600"
          }`}
          onClick={() => setSelectedCamera(camera.id)}
        >
          <CameraFeed camera={camera} />
        </div>
      ))}
    </div>
  );
}
