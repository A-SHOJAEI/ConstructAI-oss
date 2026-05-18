"use client";

import { useState, useRef, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { toast } from "sonner";
import { X } from "lucide-react";

interface CreateSubmittalDialogProps {
  projectId: string;
  onClose: () => void;
}

export function CreateSubmittalDialog({ projectId, onClose }: CreateSubmittalDialogProps) {
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

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [submittalType, setSubmittalType] = useState("shop_drawing");
  const [priority, setPriority] = useState("normal");
  const [specSection, setSpecSection] = useState("");
  const [specSectionName, setSpecSectionName] = useState("");
  const [dateRequired, setDateRequired] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [leadTimeDays, setLeadTimeDays] = useState("");

  const validate = (): string | null => {
    if (!title.trim()) return "Title is required";
    if (title.trim().length < 3) return "Title must be at least 3 characters";
    if (dueDate && new Date(dueDate) < new Date(new Date().toDateString()))
      return "Due date cannot be in the past";
    if (dateRequired && new Date(dateRequired) < new Date(new Date().toDateString()))
      return "Date required cannot be in the past";
    if (leadTimeDays && parseInt(leadTimeDays, 10) < 0)
      return "Lead time must be a positive number";
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
        title: title.trim(),
        submittal_type: submittalType,
        priority,
      };
      if (description.trim()) body.description = description.trim();
      if (specSection.trim()) body.spec_section = specSection.trim();
      if (specSectionName.trim()) body.spec_section_name = specSectionName.trim();
      if (dateRequired) body.date_required = dateRequired;
      if (dueDate) body.due_date = dueDate;
      if (leadTimeDays) body.lead_time_days = parseInt(leadTimeDays, 10);

      await apiClient.post(`/api/v1/projects/${projectId}/submittals`, body);

      queryClient.invalidateQueries({ queryKey: ["submittals"] });
      queryClient.invalidateQueries({ queryKey: ["submittal-stats"] });
      toast.success("Submittal created");
      onClose();
    } catch {
      setError("Failed to create submittal. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      role="dialog"
      aria-modal="true"
      aria-labelledby="create-submittal-dialog-title"
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
            <h2 id="create-submittal-dialog-title" className="text-lg font-semibold text-gray-900">
              New Submittal
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
              <label className="block text-sm font-medium text-gray-700 mb-1">Title *</label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                placeholder="Brief description of the submittal"
                required
                aria-required="true"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={3}
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                placeholder="Detailed description (optional)"
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Type</label>
                <select
                  value={submittalType}
                  onChange={(e) => setSubmittalType(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                >
                  <option value="shop_drawing">Shop Drawing</option>
                  <option value="product_data">Product Data</option>
                  <option value="sample">Sample</option>
                  <option value="mock_up">Mock-Up</option>
                  <option value="test_report">Test Report</option>
                  <option value="certificate">Certificate</option>
                  <option value="other">Other</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Priority</label>
                <select
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
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Spec Section</label>
                <input
                  type="text"
                  value={specSection}
                  onChange={(e) => setSpecSection(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                  placeholder="e.g., 03 30 00"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Spec Section Name
                </label>
                <input
                  type="text"
                  value={specSectionName}
                  onChange={(e) => setSpecSectionName(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                  placeholder="e.g., Cast-in-Place Concrete"
                />
              </div>
            </div>

            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Date Required
                </label>
                <input
                  type="date"
                  value={dateRequired}
                  onChange={(e) => setDateRequired(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Due Date</label>
                <input
                  type="date"
                  value={dueDate}
                  onChange={(e) => setDueDate(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Lead Time (days)
                </label>
                <input
                  type="number"
                  value={leadTimeDays}
                  onChange={(e) => setLeadTimeDays(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                  placeholder="e.g., 14"
                  min="0"
                />
              </div>
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
                disabled={submitting || !title.trim()}
                className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
              >
                {submitting ? "Creating..." : "Create Submittal"}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
