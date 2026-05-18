"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { DetectionEvent, SafetyAlert } from "@/lib/safety-api";

interface UseSafetyWebSocketOptions {
  projectId: string;
  enabled?: boolean;
  onAlert?: (alert: SafetyAlert) => void;
  onDetection?: (event: DetectionEvent) => void;
}

interface UseSafetyWebSocketReturn {
  connected: boolean;
  lastAlert: SafetyAlert | null;
  lastDetection: DetectionEvent | null;
  alerts: SafetyAlert[];
  error: string | null;
}

export function useSafetyWebSocket({
  projectId,
  enabled = true,
  onAlert,
  onDetection,
}: UseSafetyWebSocketOptions): UseSafetyWebSocketReturn {
  const [connected, setConnected] = useState(false);
  const [lastAlert, setLastAlert] = useState<SafetyAlert | null>(null);
  const [lastDetection, setLastDetection] = useState<DetectionEvent | null>(null);
  const [alerts, setAlerts] = useState<SafetyAlert[]>([]);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const authenticatedRef = useRef(false);
  const retryCountRef = useRef(0);
  const MAX_RETRIES = 7;
  const onAlertRef = useRef(onAlert);
  const onDetectionRef = useRef(onDetection);

  onAlertRef.current = onAlert;
  onDetectionRef.current = onDetection;

  const connect = useCallback(() => {
    if (!enabled || !projectId) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = process.env.NEXT_PUBLIC_WS_HOST || window.location.host;
    // Security: httpOnly cookies are sent automatically on WebSocket upgrade
    // for same-origin connections. No token in URL or message-based auth needed.
    const encodedProjectId = encodeURIComponent(projectId);
    const url = `${protocol}//${host}/api/v1/safety/ws/${encodedProjectId}`;

    const ws = new WebSocket(url);
    wsRef.current = ws;
    authenticatedRef.current = false;

    ws.onopen = () => {
      setError(null);
      retryCountRef.current = 0; // Reset retry count on successful connection
      // httpOnly cookies are sent automatically on WebSocket upgrade for
      // same-origin / wss:// connections. No manual token send needed.
      // Note: authenticatedRef and connected state are set when the server
      // sends an auth_ok message in onmessage, not here. If the server
      // validates auth purely at the upgrade handshake level (no auth_ok
      // message), the onmessage handler below will still process incoming
      // data once authenticatedRef is set by auth_ok.
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        // Wait for auth confirmation before processing other messages
        if (!authenticatedRef.current) {
          if (data.type === "auth_ok") {
            authenticatedRef.current = true;
            setConnected(true);
          } else if (data.type === "auth_error") {
            setError("WebSocket authentication failed");
            ws.close();
          }
          return;
        }

        if (data.type === "alert") {
          const alert = data.payload as SafetyAlert;
          setLastAlert(alert);
          setAlerts((prev) => [alert, ...prev].slice(0, 100));
          onAlertRef.current?.(alert);
        } else if (data.type === "detection") {
          const detection = data.payload as DetectionEvent;
          setLastDetection(detection);
          onDetectionRef.current?.(detection);
        }
      } catch {
        // ignore non-JSON messages
      }
    };

    ws.onclose = (event) => {
      setConnected(false);
      authenticatedRef.current = false;
      // Don't reconnect on auth errors (1008 = policy violation) or if server sent auth_error
      if (event.code === 1008 || event.code === 1003) {
        return;
      }
      // Exponential backoff with max retries
      if (retryCountRef.current < MAX_RETRIES) {
        const delay = Math.min(3000 * Math.pow(2, retryCountRef.current), 30000);
        retryCountRef.current += 1;
        setTimeout(connect, delay);
      } else {
        setError("WebSocket connection failed after maximum retries");
      }
    };

    ws.onerror = () => {
      setError("WebSocket connection failed");
      ws.close();
    };
  }, [enabled, projectId]);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
  }, [connect]);

  return { connected, lastAlert, lastDetection, alerts, error };
}
