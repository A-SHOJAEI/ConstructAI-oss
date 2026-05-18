"use client";
import { useEffect, useRef, useState } from "react";

interface UseCameraStreamOptions {
  streamUrl: string;
  enabled?: boolean;
}

interface UseCameraStreamReturn {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  connected: boolean;
  error: string | null;
}

export function useCameraStream({
  streamUrl,
  enabled = true,
}: UseCameraStreamOptions): UseCameraStreamReturn {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);

  useEffect(() => {
    if (!enabled || !streamUrl) return;

    const mediamtxHost = process.env.NEXT_PUBLIC_MEDIAMTX_HOST || "localhost:8889";
    const streamPath = streamUrl.replace(/^rtsp:\/\/[^/]+\//, "");
    // Validate stream path - prevent path traversal via character allowlist
    const sanitizedPath = streamPath.replace(/[^a-zA-Z0-9\-_\/]/g, "");
    if (sanitizedPath.includes("..") || sanitizedPath.startsWith("/")) {
      setError("Invalid stream path");
      return;
    }
    const protocol = typeof window !== "undefined" ? window.location.protocol : "https:";
    const whepUrl = `${protocol}//${mediamtxHost}/${sanitizedPath}/whep`;

    let pc: RTCPeerConnection | null = null;

    const connect = async () => {
      try {
        pc = new RTCPeerConnection({
          iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
        });
        pcRef.current = pc;

        pc.addTransceiver("video", { direction: "recvonly" });
        pc.addTransceiver("audio", { direction: "recvonly" });

        pc.ontrack = (event) => {
          if (videoRef.current && event.streams[0]) {
            videoRef.current.srcObject = event.streams[0];
            setConnected(true);
          }
        };

        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);

        const response = await fetch(whepUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/sdp",
          },
          credentials: "include",
          body: offer.sdp,
        });

        if (!response.ok) {
          throw new Error(`WHEP negotiation failed: ${response.status}`);
        }

        const answerSdp = await response.text();
        await pc.setRemoteDescription({
          type: "answer",
          sdp: answerSdp,
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : "Stream connection failed");
        setConnected(false);
      }
    };

    connect();

    return () => {
      pc?.close();
      pcRef.current = null;
      setConnected(false);
    };
  }, [streamUrl, enabled]);

  return { videoRef, connected, error };
}
