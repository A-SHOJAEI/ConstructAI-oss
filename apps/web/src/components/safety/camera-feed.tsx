"use client";
import { useCameraStream } from "@/hooks/use-camera-stream";
import type { Camera } from "@/lib/safety-api";

interface CameraFeedProps {
  camera: Camera;
  showOverlay?: boolean;
}

export function CameraFeed({ camera, showOverlay = true }: CameraFeedProps) {
  const { videoRef, connected, error } = useCameraStream({
    streamUrl: camera.stream_url,
    enabled: camera.status === "active",
  });

  return (
    <div className="relative aspect-video bg-gray-900 rounded-lg overflow-hidden">
      <video ref={videoRef} autoPlay muted playsInline className="w-full h-full object-cover" />

      {showOverlay && (
        <div className="absolute top-0 left-0 right-0 p-2 bg-gradient-to-b from-black/60 to-transparent">
          <div className="flex items-center justify-between">
            <span className="text-white text-sm font-medium">{camera.name}</span>
            <span
              className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                connected ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"
              }`}
            >
              {connected ? "Live" : "Offline"}
            </span>
          </div>
          {camera.location_description && (
            <p className="text-gray-300 text-xs mt-1">{camera.location_description}</p>
          )}
        </div>
      )}

      {error && (
        <div className="absolute inset-0 flex items-center justify-center bg-gray-900/80">
          <p className="text-red-400 text-sm">{error}</p>
        </div>
      )}

      {!connected && !error && camera.status === "active" && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
        </div>
      )}
    </div>
  );
}
