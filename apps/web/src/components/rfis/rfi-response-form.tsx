"use client";

import { useState } from "react";
import { apiClient } from "@/lib/api-client";
import { Send } from "lucide-react";

interface RfiResponseFormProps {
  projectId: string;
  rfiId: string;
  onSuccess?: () => void;
}

export function RfiResponseForm({ projectId, rfiId, onSuccess }: RfiResponseFormProps) {
  const [responseText, setResponseText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!responseText.trim()) return;

    setSubmitting(true);
    setError(null);

    try {
      await apiClient.post(`/api/v1/projects/${projectId}/rfis/${rfiId}/respond`, {
        response_text: responseText.trim(),
      });
      setResponseText("");
      onSuccess?.();
    } catch {
      setError("Failed to submit response.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <label className="block text-sm font-medium text-gray-700">Add Response</label>
      <textarea
        value={responseText}
        onChange={(e) => setResponseText(e.target.value)}
        rows={3}
        className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
        placeholder="Type your response..."
      />
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button
        type="submit"
        disabled={submitting || !responseText.trim()}
        className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
      >
        <Send className="h-4 w-4" />
        {submitting ? "Submitting..." : "Submit Response"}
      </button>
    </form>
  );
}
