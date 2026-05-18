"use client";

import { useState, useRef, useCallback } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";

interface VoiceRecorderProps {
  projectId: string;
  mode?: "transcribe" | "query";
  onResult?: (result: TranscriptionResult | QueryResult) => void;
}

interface TranscriptionResult {
  transcript: string;
  language: string;
  duration_seconds: number;
}

interface QueryResult {
  transcript: string;
  answer: string;
  sources: Array<{ document: string; chunk: string; score: number }>;
  confidence: number;
}

type RecordingState = "idle" | "recording" | "processing";

export function VoiceRecorder({ projectId, mode = "transcribe", onResult }: VoiceRecorderProps) {
  const [state, setState] = useState<RecordingState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TranscriptionResult | QueryResult | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const transcribeMutation = useMutation({
    mutationFn: async (audioBlob: Blob): Promise<TranscriptionResult | QueryResult> => {
      const formData = new FormData();
      formData.append("file", audioBlob, "recording.webm");
      const endpoint =
        mode === "query"
          ? `/api/v1/projects/${projectId}/voice/query`
          : `/api/v1/projects/${projectId}/voice/transcribe`;
      return apiClient.upload<TranscriptionResult | QueryResult>(endpoint, formData);
    },
    onSuccess: (data: TranscriptionResult | QueryResult) => {
      setResult(data);
      onResult?.(data);
      setState("idle");
    },
    onError: (err: Error) => {
      setError(err.message);
      setState("idle");
    },
  });

  const startRecording = useCallback(async () => {
    setError(null);
    setResult(null);

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream, {
        mimeType: MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "audio/mp4",
      });

      chunksRef.current = [];
      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      mediaRecorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, {
          type: mediaRecorder.mimeType,
        });
        // M-36: reject oversized recordings client-side so users get
        // immediate feedback instead of a 10-minute upload → 413.
        // 50 MB lines up with the backend body-size cap; keep in sync.
        const MAX_BLOB_BYTES = 50 * 1024 * 1024;
        if (blob.size > MAX_BLOB_BYTES) {
          setError(
            `Recording is too large (${Math.round(blob.size / (1024 * 1024))} MB). ` +
              `Maximum is 50 MB — try recording a shorter clip.`,
          );
          setState("idle");
          return;
        }
        setState("processing");
        transcribeMutation.mutate(blob);
      };

      mediaRecorderRef.current = mediaRecorder;
      mediaRecorder.start(1000);
      setState("recording");
    } catch {
      setError("Microphone access denied. Please allow microphone permissions.");
    }
  }, [transcribeMutation]);

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.stop();
    }
  }, []);

  return (
    <div className="rounded-lg border border-gray-200 p-4 dark:border-gray-700">
      <div className="flex items-center gap-3">
        {state === "idle" && (
          <button
            onClick={startRecording}
            aria-label={mode === "query" ? "Start recording to ask a question" : "Start recording"}
            className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 20 20" aria-hidden="true">
              <path
                fillRule="evenodd"
                d="M7 4a3 3 0 016 0v4a3 3 0 11-6 0V4zm4 10.93A7.001 7.001 0 0017 8a1 1 0 10-2 0A5 5 0 015 8a1 1 0 00-2 0 7.001 7.001 0 006 6.93V17H6a1 1 0 100 2h8a1 1 0 100-2h-3v-2.07z"
                clipRule="evenodd"
              />
            </svg>
            {mode === "query" ? "Ask a Question" : "Record"}
          </button>
        )}

        {state === "recording" && (
          <button
            onClick={stopRecording}
            aria-label="Stop recording"
            className="flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
          >
            <span
              className="h-3 w-3 animate-pulse rounded-full bg-white"
              role="status"
              aria-label="Recording in progress"
            />
            Stop Recording
          </button>
        )}

        {state === "processing" && (
          <div
            className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400"
            role="status"
            aria-live="assertive"
          >
            <svg
              className="h-4 w-4 animate-spin"
              fill="none"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
              />
            </svg>
            Processing...
          </div>
        )}
      </div>

      {error && (
        <p
          className="mt-2 text-sm text-red-600 dark:text-red-400"
          role="alert"
          aria-live="assertive"
        >
          {error}
        </p>
      )}

      {result && "transcript" in result && (
        <div className="mt-3 space-y-2">
          <div className="rounded bg-gray-50 p-3 dark:bg-gray-800">
            <p className="text-xs font-medium text-gray-500 dark:text-gray-400">Transcript</p>
            <p className="mt-1 text-sm text-gray-900 dark:text-gray-100">{result.transcript}</p>
          </div>

          {"answer" in result && (
            <div className="rounded bg-blue-50 p-3 dark:bg-blue-900/20">
              <p className="text-xs font-medium text-blue-600 dark:text-blue-400">Answer</p>
              <p className="mt-1 text-sm text-gray-900 dark:text-gray-100">
                {(result as QueryResult).answer}
              </p>
              {(result as QueryResult).sources.length > 0 && (
                <div className="mt-2">
                  <p className="text-xs text-gray-500">Sources:</p>
                  <ul className="mt-1 space-y-1">
                    {(result as QueryResult).sources.map((s, i) => (
                      <li key={i} className="text-xs text-gray-600 dark:text-gray-400">
                        {s.document} (score: {s.score})
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
