"use client";

/**
 * Global "Ask ConstructAI" widget — a floating panel available on every
 * dashboard page. Collapsed state shows a small button bottom-right; the
 * expanded state is a chat-like panel with question history, the most
 * recent answer rendered as Markdown-ish text, and the cited data sources
 * pulled from the corpus.
 *
 * State is persisted in localStorage so the widget remembers whether
 * the user had it open across page navigations and refreshes.
 *
 * Wires to: POST /api/v1/projects/{project_id}/ask
 *   request:  { question: string }
 *   response: { answer, intent, confidence, data_sources, ... }
 */

import { useEffect, useRef, useState } from "react";
import { MessageCircleQuestion, X, Send, Sparkles, Loader2 } from "lucide-react";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";

interface AskResponse {
  answer: string;
  intent?: string;
  confidence?: number;
  data_sources?: string[];
  follow_up_suggestions?: string[];
  processing_time_ms?: number;
}

interface QAExchange {
  id: string;
  question: string;
  pending: boolean;
  answer?: string;
  data_sources?: string[];
  confidence?: number;
  intent?: string;
  error?: string;
  elapsedMs?: number;
}

const STORAGE_KEY = "ask-widget-open";

export function AskWidget() {
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const [exchanges, setExchanges] = useState<QAExchange[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const project = useProjectStore((s) => s.selectedProject);
  const projectId = useProjectStore((s) => s.selectedProjectId);

  // Hydrate open state from localStorage on first mount.
  useEffect(() => {
    try {
      const v = window.localStorage.getItem(STORAGE_KEY);
      if (v === "true") setOpen(true);
    } catch {
      // localStorage may be disabled (private mode); ignore.
    }
  }, []);

  // Persist open state.
  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, open ? "true" : "false");
    } catch {
      // ignore
    }
  }, [open]);

  // Auto-scroll the conversation pane on new exchanges.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [exchanges]);

  // Focus the textarea when the panel opens.
  useEffect(() => {
    if (open && inputRef.current) {
      inputRef.current.focus();
    }
  }, [open]);

  // Esc closes the panel.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  async function submit() {
    const q = question.trim();
    if (!q || submitting) return;
    if (!projectId) {
      // Without a project context the /ask endpoint 404s.
      const id = crypto.randomUUID();
      setExchanges((prev) => [
        ...prev,
        {
          id,
          question: q,
          pending: false,
          error: "Select a project first — Ask is project-scoped.",
        },
      ]);
      setQuestion("");
      return;
    }
    const id = crypto.randomUUID();
    const started = performance.now();
    setExchanges((prev) => [...prev, { id, question: q, pending: true }]);
    setQuestion("");
    setSubmitting(true);
    try {
      const data = await apiClient.post<AskResponse>(
        `/api/v1/projects/${projectId}/ask`,
        { question: q },
        { timeoutMs: 120_000 },
      );
      const elapsed = Math.round(performance.now() - started);
      setExchanges((prev) =>
        prev.map((e) =>
          e.id === id
            ? {
                ...e,
                pending: false,
                answer: data.answer,
                data_sources: data.data_sources,
                confidence: data.confidence,
                intent: data.intent,
                elapsedMs: elapsed,
              }
            : e,
        ),
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Request failed";
      setExchanges((prev) =>
        prev.map((e) => (e.id === id ? { ...e, pending: false, error: msg } : e)),
      );
    } finally {
      setSubmitting(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  // Collapsed: small button bottom-right
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed bottom-6 right-6 z-40 flex items-center gap-2 rounded-full bg-primary px-4 py-3 text-white shadow-lg hover:bg-primary-dark hover:shadow-xl transition-all focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2"
        aria-label="Open Ask ConstructAI"
        title="Ask ConstructAI (against your project corpus)"
      >
        <Sparkles className="h-5 w-5" />
        <span className="font-medium">Ask AI</span>
      </button>
    );
  }

  // Expanded: floating panel bottom-right
  return (
    <div
      className="fixed bottom-6 right-6 z-40 flex w-[28rem] max-w-[calc(100vw-3rem)] flex-col rounded-xl border border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-800"
      style={{ height: "min(36rem, calc(100vh - 3rem))" }}
      role="dialog"
      aria-label="Ask ConstructAI"
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3 dark:border-gray-700">
        <div className="flex items-center gap-2">
          <MessageCircleQuestion className="h-5 w-5 text-primary" />
          <div>
            <div className="text-sm font-semibold text-gray-900 dark:text-white">
              Ask ConstructAI
            </div>
            <div className="text-xs text-gray-500 dark:text-gray-400">
              {project?.name
                ? `Scoped to "${project.name}" · grounded in project corpus`
                : "No project selected — pick one in the header"}
            </div>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-700 dark:hover:text-gray-200"
          aria-label="Close"
        >
          <X className="h-5 w-5" />
        </button>
      </div>

      {/* Conversation */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        {exchanges.length === 0 && (
          <div className="text-sm text-gray-500 dark:text-gray-400">
            <p className="mb-2">Ask anything about this project — RFIs, specs, OSHA, costs.</p>
            <p className="font-medium text-gray-700 dark:text-gray-300">Try:</p>
            <ul className="mt-1 list-disc list-inside space-y-1">
              <li>What does UFGS 03 30 00 say about cure time?</li>
              <li>Summarize the open RFIs by trade.</li>
              <li>What OSHA standards apply to scaffold work above 10 ft?</li>
            </ul>
          </div>
        )}
        {exchanges.map((ex) => (
          <div key={ex.id} className="space-y-2">
            <div className="rounded-lg bg-primary/10 px-3 py-2 text-sm text-gray-900 dark:bg-primary/20 dark:text-white">
              <div className="font-medium text-xs text-primary uppercase tracking-wide mb-1">
                You
              </div>
              {ex.question}
            </div>
            <div className="rounded-lg bg-gray-50 px-3 py-2 text-sm text-gray-900 dark:bg-gray-900 dark:text-white">
              <div className="flex items-center justify-between mb-1">
                <span className="font-medium text-xs text-gray-500 uppercase tracking-wide">
                  ConstructAI
                </span>
                {!ex.pending && ex.confidence != null && (
                  <span className="text-xs text-gray-400">
                    {(ex.confidence * 100).toFixed(0)}% conf
                    {ex.elapsedMs ? ` · ${(ex.elapsedMs / 1000).toFixed(1)}s` : ""}
                  </span>
                )}
              </div>
              {ex.pending ? (
                <div className="flex items-center gap-2 text-gray-500">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span>Searching corpus + drafting…</span>
                </div>
              ) : ex.error ? (
                <div className="text-red-600 dark:text-red-400">{ex.error}</div>
              ) : (
                <>
                  <div className="whitespace-pre-wrap text-sm leading-relaxed">{ex.answer}</div>
                  {ex.data_sources && ex.data_sources.length > 0 && (
                    <div className="mt-2 border-t border-gray-200 pt-2 dark:border-gray-700">
                      <div className="text-xs text-gray-500 mb-1">Sources</div>
                      <div className="flex flex-wrap gap-1">
                        {ex.data_sources.slice(0, 6).map((s, i) => (
                          <span
                            key={i}
                            className="inline-block rounded bg-gray-200 px-2 py-0.5 text-xs text-gray-700 dark:bg-gray-700 dark:text-gray-300"
                            title={s}
                          >
                            {s.length > 32 ? s.slice(0, 30) + "…" : s}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Input */}
      <div className="border-t border-gray-200 p-3 dark:border-gray-700">
        <div className="relative">
          <textarea
            ref={inputRef}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={submitting}
            placeholder={
              projectId
                ? "Ask anything about this project…"
                : "Select a project to enable Ask"
            }
            className="block w-full resize-none rounded-lg border border-gray-300 px-3 py-2 pr-10 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary dark:border-gray-600 dark:bg-gray-900 dark:text-white"
            rows={2}
          />
          <button
            type="button"
            onClick={submit}
            disabled={!question.trim() || submitting}
            className="absolute bottom-2 right-2 rounded p-1 text-primary hover:bg-primary/10 disabled:cursor-not-allowed disabled:text-gray-300 dark:disabled:text-gray-600"
            aria-label="Send"
            title="Send (Enter)"
          >
            {submitting ? (
              <Loader2 className="h-5 w-5 animate-spin" />
            ) : (
              <Send className="h-5 w-5" />
            )}
          </button>
        </div>
        <div className="mt-1 text-[10px] text-gray-400">
          Enter to send · Shift+Enter for new line · Esc to close
        </div>
      </div>
    </div>
  );
}
