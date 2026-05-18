"use client";

import { useState, useRef, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { toast } from "sonner";
import { X } from "lucide-react";

interface CreateRfiDialogProps {
  projectId: string;
  onClose: () => void;
}

export function CreateRfiDialog({ projectId, onClose }: CreateRfiDialogProps) {
  const queryClient = useQueryClient();
  const triggerRef = useRef<HTMLElement | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);

  useEffect(() => {
    triggerRef.current = document.activeElement as HTMLElement;
    return () => {
      triggerRef.current?.focus();
    };
  }, []);

  const [subject, setSubject] = useState("");
  const [question, setQuestion] = useState("");
  const [priority, setPriority] = useState("normal");
  const [dueDate, setDueDate] = useState("");
  const [specSection, setSpecSection] = useState("");
  const [drawingReference, setDrawingReference] = useState("");
  const [costImpact, setCostImpact] = useState(false);
  const [scheduleImpact, setScheduleImpact] = useState(false);

  const validate = (): string | null => {
    if (!subject.trim()) return "Subject is required";
    if (!question.trim()) return "Question is required";
    if (subject.trim().length < 3) return "Subject must be at least 3 characters";
    if (question.trim().length < 10) return "Question must be at least 10 characters";
    if (dueDate && new Date(dueDate) < new Date(new Date().toDateString()))
      return "Due date cannot be in the past";
    return null;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    const validationErr = validate();
    if (validationErr) {
      setValidationError(validationErr);
      return;
    }
    setValidationError(null);

    setSubmitting(true);
    setError(null);

    try {
      const body: Record<string, unknown> = {
        subject: subject.trim(),
        question: question.trim(),
        priority,
        cost_impact: costImpact || null,
        schedule_impact: scheduleImpact || null,
      };
      if (dueDate) body.due_date = dueDate;
      if (specSection.trim()) body.spec_section = specSection.trim();
      if (drawingReference.trim()) body.drawing_reference = drawingReference.trim();

      await apiClient.post(`/api/v1/projects/${projectId}/rfis`, body);

      queryClient.invalidateQueries({ queryKey: ["rfis"] });
      queryClient.invalidateQueries({ queryKey: ["rfi-stats"] });
      toast.success("RFI created successfully");
      onClose();
    } catch {
      setError("Failed to create RFI. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      role="dialog"
      aria-modal="true"
      aria-labelledby="create-rfi-dialog-title"
      onKeyDown={(e) => {
        if (e.key === "Tab") {
          const focusable = e.currentTarget.querySelectorAll<HTMLElement>(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
          );
          if (focusable.length === 0) return;
          const first = focusable[0];
          const last = focusable[focusable.length - 1];
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        }
        if (e.key === "Escape") {
          onClose();
        }
      }}
    >
      <div className="bg-white rounded-lg shadow-xl max-w-lg w-full mx-4 max-h-[90vh] overflow-y-auto">
        <div className="p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 id="create-rfi-dialog-title" className="text-lg font-semibold text-gray-900">
              New RFI
            </h2>
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600"
              aria-label="Close dialog"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          {error && <div className="mb-4 p-3 text-sm text-red-800 bg-red-50 rounded">{error}</div>}
          {validationError && (
            <div className="mb-4 p-3 text-sm text-amber-800 bg-amber-50 rounded">
              {validationError}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="rfi-subject" className="block text-sm font-medium text-gray-700 mb-1">
                Subject *
              </label>
              <input
                id="rfi-subject"
                type="text"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                placeholder="Brief description of the RFI"
                required
                aria-required="true"
                maxLength={300}
              />
            </div>

            <div>
              <label
                htmlFor="rfi-question"
                className="block text-sm font-medium text-gray-700 mb-1"
              >
                Question *
              </label>
              <textarea
                id="rfi-question"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                rows={4}
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                placeholder="Detailed question or clarification needed"
                required
                aria-required="true"
                maxLength={10000}
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label
                  htmlFor="rfi-priority"
                  className="block text-sm font-medium text-gray-700 mb-1"
                >
                  Priority
                </label>
                <select
                  id="rfi-priority"
                  value={priority}
                  onChange={(e) => setPriority(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                >
                  <option value="low">Low</option>
                  <option value="normal">Normal</option>
                  <option value="high">High</option>
                  <option value="urgent">Urgent</option>
                </select>
              </div>
              <div>
                <label
                  htmlFor="rfi-due-date"
                  className="block text-sm font-medium text-gray-700 mb-1"
                >
                  Due Date
                </label>
                <input
                  id="rfi-due-date"
                  type="date"
                  value={dueDate}
                  onChange={(e) => setDueDate(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label
                  htmlFor="rfi-spec-section"
                  className="block text-sm font-medium text-gray-700 mb-1"
                >
                  Spec Section
                </label>
                <input
                  id="rfi-spec-section"
                  type="text"
                  value={specSection}
                  onChange={(e) => setSpecSection(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                  placeholder="e.g., 03 30 00"
                  maxLength={100}
                />
              </div>
              <div>
                <label
                  htmlFor="rfi-drawing-ref"
                  className="block text-sm font-medium text-gray-700 mb-1"
                >
                  Drawing Reference
                </label>
                <input
                  id="rfi-drawing-ref"
                  type="text"
                  value={drawingReference}
                  onChange={(e) => setDrawingReference(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                  placeholder="e.g., A-201"
                  maxLength={100}
                />
              </div>
            </div>

            <div className="flex gap-6">
              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={costImpact}
                  onChange={(e) => setCostImpact(e.target.checked)}
                  className="rounded border-gray-300"
                />
                Cost Impact
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={scheduleImpact}
                  onChange={(e) => setScheduleImpact(e.target.checked)}
                  className="rounded border-gray-300"
                />
                Schedule Impact
              </label>
            </div>

            <div className="flex gap-2 pt-4 border-t border-gray-200">
              <button
                type="button"
                onClick={onClose}
                className="flex-1 px-4 py-2 border border-gray-300 rounded-md text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting || !subject.trim() || !question.trim()}
                className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
              >
                {submitting ? "Creating..." : "Create RFI"}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
