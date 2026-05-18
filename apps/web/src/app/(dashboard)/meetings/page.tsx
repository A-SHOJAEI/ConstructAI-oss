"use client";

import { useQuery } from "@tanstack/react-query";
import { meetingsApi } from "@/lib/meetings-api";
import type { MeetingMinutes } from "@/lib/meetings-api";
import { Users, Calendar, MapPin, Clock, AlertCircle, CheckCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";

const typeColors: Record<string, string> = {
  weekly: "bg-blue-100 text-blue-800",
  safety: "bg-red-100 text-red-800",
  owner: "bg-purple-100 text-purple-800",
  subcontractor: "bg-green-100 text-green-800",
  design: "bg-amber-100 text-amber-800",
  kickoff: "bg-indigo-100 text-indigo-800",
  closeout: "bg-gray-100 text-gray-800",
};

const statusColors: Record<string, string> = {
  draft: "bg-gray-100 text-gray-700",
  final: "bg-green-100 text-green-800",
  distributed: "bg-blue-100 text-blue-800",
};

export default function MeetingsPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const [typeFilter, setTypeFilter] = useState("");
  const [selectedMeeting, setSelectedMeeting] = useState<MeetingMinutes | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["meetings", projectId],
    queryFn: () => meetingsApi.list(projectId!),
    enabled: !!projectId,
  });

  const meetings = data?.data ?? [];

  // Close modal on Escape key — must be before any early returns to avoid
  // conditional hook ordering (React Rules of Hooks).
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelectedMeeting(null);
    };
    if (selectedMeeting) {
      document.addEventListener("keydown", handleKeyDown);
      return () => document.removeEventListener("keydown", handleKeyDown);
    }
  }, [selectedMeeting]);

  if (!projectId) return <NoProjectSelected />;

  const types = [...new Set(meetings.map((m) => m.meeting_type))];

  const filtered = typeFilter ? meetings.filter((m) => m.meeting_type === typeFilter) : meetings;

  const getOverdueCount = (meeting: MeetingMinutes) => {
    const items = meeting.action_items as { due_date?: string; status?: string }[];
    const today = new Date().toISOString().slice(0, 10);
    return items.filter(
      (item) => item.due_date && item.due_date < today && item.status !== "completed",
    ).length;
  };

  return (
    <div className="p-4 md:p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Users className="h-6 w-6 text-blue-600" />
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Meeting Minutes</h1>
            <p className="text-sm text-gray-500">{meetings.length} meetings recorded</p>
          </div>
        </div>

        {types.length > 1 && (
          <div className="flex gap-1">
            <button
              onClick={() => setTypeFilter("")}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium ${
                !typeFilter
                  ? "bg-gray-900 text-white"
                  : "bg-gray-100 text-gray-600 hover:bg-gray-200"
              }`}
            >
              All
            </button>
            {types.map((t) => (
              <button
                key={t}
                onClick={() => setTypeFilter(t)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium capitalize ${
                  typeFilter === t
                    ? "bg-gray-900 text-white"
                    : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                }`}
              >
                {t}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Meeting Cards */}
      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="bg-white rounded-lg border border-gray-200 p-4 animate-pulse">
              <div className="h-5 bg-gray-200 rounded w-48 mb-3" />
              <div className="h-4 bg-gray-100 rounded w-32" />
            </div>
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="bg-white rounded-lg border border-gray-200 p-12 text-center">
          <Users className="h-10 w-10 text-gray-300 mx-auto mb-2" />
          <p className="text-gray-500 text-sm">No meetings found</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {filtered.map((meeting) => {
            const actionItems = meeting.action_items as { status?: string }[];
            const overdueCount = getOverdueCount(meeting);
            const completedCount = actionItems.filter((i) => i.status === "completed").length;

            return (
              <button
                key={meeting.id}
                onClick={() => setSelectedMeeting(meeting)}
                className="bg-white rounded-lg border border-gray-200 p-4 text-left hover:shadow-md transition-shadow"
              >
                <div className="flex items-start justify-between mb-2">
                  <h3 className="text-sm font-semibold text-gray-900 line-clamp-2">
                    {meeting.title}
                  </h3>
                  <div className="flex gap-1 shrink-0 ml-2">
                    <span
                      className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium capitalize ${typeColors[meeting.meeting_type] ?? "bg-gray-100 text-gray-700"}`}
                    >
                      {meeting.meeting_type}
                    </span>
                    <span
                      className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium capitalize ${statusColors[meeting.status] ?? "bg-gray-100 text-gray-700"}`}
                    >
                      {meeting.status}
                    </span>
                  </div>
                </div>

                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500 mb-3">
                  <span className="flex items-center gap-1">
                    <Calendar className="h-3 w-3" />
                    {new Date(meeting.meeting_date).toLocaleDateString()}
                  </span>
                  {meeting.meeting_location && (
                    <span className="flex items-center gap-1">
                      <MapPin className="h-3 w-3" /> {meeting.meeting_location}
                    </span>
                  )}
                  {meeting.start_time && (
                    <span className="flex items-center gap-1">
                      <Clock className="h-3 w-3" /> {meeting.start_time}
                    </span>
                  )}
                  <span className="flex items-center gap-1">
                    <Users className="h-3 w-3" /> {meeting.attendees.length} attendees
                  </span>
                </div>

                {meeting.summary && (
                  <p className="text-xs text-gray-600 mb-3 line-clamp-2">{meeting.summary}</p>
                )}

                {actionItems.length > 0 && (
                  <div className="flex items-center gap-3 text-xs border-t border-gray-100 pt-2">
                    <span className="text-gray-500">
                      {actionItems.length} action item{actionItems.length !== 1 && "s"}
                    </span>
                    {completedCount > 0 && (
                      <span className="flex items-center gap-0.5 text-green-600">
                        <CheckCircle className="h-3 w-3" /> {completedCount}
                      </span>
                    )}
                    {overdueCount > 0 && (
                      <span className="flex items-center gap-0.5 text-red-600">
                        <AlertCircle className="h-3 w-3" /> {overdueCount} overdue
                      </span>
                    )}
                  </div>
                )}
              </button>
            );
          })}
        </div>
      )}

      {/* Detail Modal */}
      {selectedMeeting && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          role="dialog"
          aria-modal="true"
          aria-labelledby="meeting-modal-title"
          onClick={(e) => {
            if (e.target === e.currentTarget) setSelectedMeeting(null);
          }}
        >
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-xl w-full max-w-2xl m-4 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-700">
              <h3 id="meeting-modal-title" className="font-semibold text-gray-900 dark:text-white">
                {selectedMeeting.title}
              </h3>
              <button
                onClick={() => setSelectedMeeting(null)}
                className="text-gray-400 hover:text-gray-600 text-xl leading-none"
                aria-label="Close meeting details"
              >
                &times;
              </button>
            </div>

            <div className="p-4 space-y-4">
              <div className="flex flex-wrap gap-3 text-sm text-gray-600">
                <span className="flex items-center gap-1">
                  <Calendar className="h-4 w-4" />
                  {new Date(selectedMeeting.meeting_date).toLocaleDateString()}
                </span>
                {selectedMeeting.meeting_location && (
                  <span className="flex items-center gap-1">
                    <MapPin className="h-4 w-4" /> {selectedMeeting.meeting_location}
                  </span>
                )}
                {selectedMeeting.start_time && selectedMeeting.end_time && (
                  <span className="flex items-center gap-1">
                    <Clock className="h-4 w-4" /> {selectedMeeting.start_time} –{" "}
                    {selectedMeeting.end_time}
                  </span>
                )}
              </div>

              {/* Attendees */}
              <div>
                <h4 className="text-sm font-medium text-gray-700 mb-2">
                  Attendees ({selectedMeeting.attendees.length})
                </h4>
                <div className="flex flex-wrap gap-1.5">
                  {selectedMeeting.attendees.map((a, i) => (
                    <span
                      key={i}
                      className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-gray-100 text-gray-700"
                    >
                      {(a as { name?: string }).name ?? `Attendee ${i + 1}`}
                    </span>
                  ))}
                </div>
              </div>

              {/* Agenda Items */}
              {selectedMeeting.agenda_items.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium text-gray-700 mb-2">Agenda</h4>
                  <ol className="space-y-1.5 list-decimal list-inside text-sm text-gray-600">
                    {selectedMeeting.agenda_items.map((item, i) => (
                      <li key={i}>{(item as { topic?: string }).topic ?? `Item ${i + 1}`}</li>
                    ))}
                  </ol>
                </div>
              )}

              {/* Notes */}
              {selectedMeeting.notes && (
                <div>
                  <h4 className="text-sm font-medium text-gray-700 mb-2">Notes</h4>
                  <p className="text-sm text-gray-600 whitespace-pre-wrap">
                    {selectedMeeting.notes}
                  </p>
                </div>
              )}

              {/* Action Items */}
              {selectedMeeting.action_items.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium text-gray-700 mb-2">
                    Action Items ({selectedMeeting.action_items.length})
                  </h4>
                  <div className="space-y-2">
                    {selectedMeeting.action_items.map((item, i) => {
                      const ai = item as {
                        description?: string;
                        assignee?: string;
                        due_date?: string;
                        status?: string;
                      };
                      return (
                        <div
                          key={i}
                          className="flex items-start gap-2 p-2 bg-gray-50 rounded-lg text-sm"
                        >
                          <div
                            className={`mt-1 w-2 h-2 rounded-full shrink-0 ${
                              ai.status === "completed"
                                ? "bg-green-500"
                                : ai.due_date &&
                                    ai.due_date < new Date().toISOString().slice(0, 10) &&
                                    ai.status !== "completed"
                                  ? "bg-red-500"
                                  : "bg-yellow-500"
                            }`}
                          />
                          <div className="flex-1 min-w-0">
                            <p className="text-gray-700">{ai.description ?? `Action ${i + 1}`}</p>
                            <div className="flex gap-3 text-xs text-gray-500 mt-0.5">
                              {ai.assignee && <span>Assigned: {ai.assignee}</span>}
                              {ai.due_date && (
                                <span>Due: {new Date(ai.due_date).toLocaleDateString()}</span>
                              )}
                              {ai.status && <span className="capitalize">{ai.status}</span>}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Decisions */}
              {selectedMeeting.decisions.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium text-gray-700 mb-2">Decisions</h4>
                  <ul className="space-y-1.5 text-sm text-gray-600">
                    {selectedMeeting.decisions.map((d, i) => (
                      <li key={i} className="flex items-start gap-2">
                        <CheckCircle className="h-4 w-4 text-green-500 mt-0.5 shrink-0" />
                        {(d as { decision?: string }).decision ?? `Decision ${i + 1}`}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
