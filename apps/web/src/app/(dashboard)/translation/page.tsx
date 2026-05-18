"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { Languages, ArrowRight, Copy, Loader2, ToggleLeft, ToggleRight } from "lucide-react";
import { toast } from "sonner";

interface TranslationResult {
  translated_text: string;
  source_language: string;
  target_language: string;
  confidence: number;
  cached: boolean;
}

const LANGUAGES = [
  { code: "en", name: "English" },
  { code: "es", name: "Spanish" },
  { code: "pt", name: "Portuguese" },
  { code: "zh", name: "Chinese" },
  { code: "ko", name: "Korean" },
  { code: "vi", name: "Vietnamese" },
  { code: "tl", name: "Tagalog" },
  { code: "fr", name: "French" },
];

// Backend allows: safety_alert | daily_log | rfi | meeting_minutes | general
const CONTEXTS = [
  { value: "general", label: "General Construction" },
  { value: "safety_alert", label: "Safety / OSHA" },
  { value: "daily_log", label: "Daily Log / Field Report" },
  { value: "rfi", label: "RFI / Submittal" },
  { value: "meeting_minutes", label: "Meeting Minutes" },
];

export default function TranslationPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const [inputText, setInputText] = useState("");
  const [targetLang, setTargetLang] = useState("es");
  const [context, setContext] = useState("general");
  const [batchMode, setBatchMode] = useState(false);
  const [batchTargets, setBatchTargets] = useState<string[]>(["es", "zh"]);

  const translateMutation = useMutation({
    mutationFn: (payload: { text: string; target_language: string; context: string }) =>
      apiClient.post<TranslationResult>(
        `/api/v1/translation/translate`,
        payload,
        { timeoutMs: 60_000 },
      ),
  });

  const batchMutation = useMutation({
    mutationFn: async (payload: {
      text: string;
      target_languages: string[];
      context: string;
    }) => {
      // Backend `/translate/batch` is many-texts-to-one-language; the UI's
      // "batch" semantic is one-text-to-many-languages. Run parallel singles.
      const results = await Promise.all(
        payload.target_languages.map((lang) =>
          apiClient.post<TranslationResult>(
            `/api/v1/translation/translate`,
            { text: payload.text, target_language: lang, context: payload.context },
            { timeoutMs: 60_000 },
          ),
        ),
      );
      return { results, total: results.length };
    },
  });

  const handleTranslate = () => {
    if (!inputText.trim()) return;
    if (batchMode) {
      batchMutation.mutate({ text: inputText.trim(), target_languages: batchTargets, context });
    } else {
      translateMutation.mutate({ text: inputText.trim(), target_language: targetLang, context });
    }
  };

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success("Copied to clipboard");
  };

  const toggleBatchLang = (code: string) => {
    setBatchTargets((prev) =>
      prev.includes(code) ? prev.filter((l) => l !== code) : [...prev, code],
    );
  };

  if (!projectId) return <NoProjectSelected />;

  const isPending = translateMutation.isPending || batchMutation.isPending;

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Translation</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Construction-context-aware translation for field communication
        </p>
      </div>

      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6 space-y-4">
        {/* Controls */}
        <div className="flex flex-wrap gap-4 items-end">
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Context</label>
            <select
              value={context}
              onChange={(e) => setContext(e.target.value)}
              className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
            >
              {CONTEXTS.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>

          {!batchMode && (
            <div>
              <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">
                Target Language
              </label>
              <select
                value={targetLang}
                onChange={(e) => setTargetLang(e.target.value)}
                className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
              >
                {LANGUAGES.map((l) => (
                  <option key={l.code} value={l.code}>
                    {l.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          <button
            onClick={() => setBatchMode(!batchMode)}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 dark:text-gray-300 hover:text-gray-900"
          >
            {batchMode ? (
              <ToggleRight className="h-5 w-5 text-blue-500" />
            ) : (
              <ToggleLeft className="h-5 w-5" />
            )}
            Batch Mode
          </button>
        </div>

        {/* Batch Language Selector */}
        {batchMode && (
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-2">
              Target Languages
            </label>
            <div className="flex flex-wrap gap-2">
              {LANGUAGES.map((l) => (
                <button
                  key={l.code}
                  onClick={() => toggleBatchLang(l.code)}
                  className={`px-3 py-1 rounded-full text-sm font-medium transition-colors ${
                    batchTargets.includes(l.code)
                      ? "bg-blue-600 text-white"
                      : "bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300"
                  }`}
                >
                  {l.name}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Input */}
        <div>
          <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Source Text</label>
          <textarea
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            placeholder="Enter text to translate (e.g., safety briefing, RFI response, daily log entry)..."
            rows={5}
            className="w-full px-4 py-3 border border-gray-300 dark:border-gray-700 rounded-lg text-sm dark:bg-gray-700 dark:text-gray-200 resize-y"
          />
        </div>

        <button
          onClick={handleTranslate}
          disabled={isPending || !inputText.trim() || (batchMode && batchTargets.length === 0)}
          className="flex items-center gap-2 px-6 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
        >
          {isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Languages className="h-4 w-4" />
          )}
          {isPending ? "Translating..." : "Translate"}
        </button>

        {/* Error */}
        {(translateMutation.error || batchMutation.error) && (
          <div className="p-3 text-red-800 bg-red-50 rounded-lg text-sm">
            Translation failed: {((translateMutation.error || batchMutation.error) as Error).message}
          </div>
        )}
      </div>

      {/* Single Result */}
      {translateMutation.data && !batchMode && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
              <span>
                {LANGUAGES.find((l) => l.code === translateMutation.data.source_language)?.name ??
                  translateMutation.data.source_language}
              </span>
              <ArrowRight className="h-4 w-4" />
              <span className="font-medium text-gray-900 dark:text-white">
                {LANGUAGES.find((l) => l.code === translateMutation.data.target_language)?.name ??
                  translateMutation.data.target_language}
              </span>
            </div>
            <button
              onClick={() => handleCopy(translateMutation.data!.translated_text)}
              className="flex items-center gap-1 px-3 py-1 text-xs text-gray-600 dark:text-gray-300 border border-gray-300 dark:border-gray-700 rounded hover:bg-gray-50 dark:hover:bg-gray-700"
            >
              <Copy className="h-3 w-3" /> Copy
            </button>
          </div>
          <p className="text-sm text-gray-900 dark:text-white whitespace-pre-wrap">
            {translateMutation.data.translated_text}
          </p>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">
            Confidence: {(translateMutation.data.confidence * 100).toFixed(0)}%
            {translateMutation.data.cached ? " (cached)" : ""}
          </p>
        </div>
      )}

      {/* Batch Results */}
      {batchMutation.data && batchMode && (
        <div className="space-y-4">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
            Batch Results ({batchMutation.data.total} translations)
          </h2>
          {batchMutation.data.results.map((result, idx) => (
            <div
              key={idx}
              className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4"
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium text-blue-600">
                  {LANGUAGES.find((l) => l.code === result.target_language)?.name ??
                    result.target_language}
                </span>
                <button
                  onClick={() => handleCopy(result.translated_text)}
                  className="flex items-center gap-1 px-2 py-0.5 text-xs text-gray-600 dark:text-gray-300 border border-gray-300 dark:border-gray-700 rounded hover:bg-gray-50 dark:hover:bg-gray-700"
                >
                  <Copy className="h-3 w-3" /> Copy
                </button>
              </div>
              <p className="text-sm text-gray-900 dark:text-white whitespace-pre-wrap">
                {result.translated_text}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
