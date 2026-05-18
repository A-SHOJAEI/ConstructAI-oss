"use client";

import { useEffect, useCallback, useState } from "react";

interface Shortcut {
  key: string;
  ctrl?: boolean;
  shift?: boolean;
  alt?: boolean;
  description: string;
  action: () => void;
}

const GLOBAL_SHORTCUTS: Shortcut[] = [];

export function useKeyboardShortcut(
  key: string,
  callback: () => void,
  options?: { ctrl?: boolean; shift?: boolean; alt?: boolean },
) {
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if (
        e.key.toLowerCase() === key.toLowerCase() &&
        !!e.ctrlKey === !!options?.ctrl &&
        !!e.shiftKey === !!options?.shift &&
        !!e.altKey === !!options?.alt
      ) {
        const target = e.target as HTMLElement;
        if (
          target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable
        ) {
          return;
        }
        e.preventDefault();
        callback();
      }
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [key, callback, options?.ctrl, options?.shift, options?.alt]);
}

export function useGlobalShortcuts(shortcuts: Shortcut[]) {
  useEffect(() => {
    GLOBAL_SHORTCUTS.length = 0;
    GLOBAL_SHORTCUTS.push(...shortcuts);
    return () => {
      GLOBAL_SHORTCUTS.length = 0;
    };
  }, [shortcuts]);
}

export function KeyboardShortcutsHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null;

  const shortcuts = [
    { keys: "?", description: "Show keyboard shortcuts" },
    { keys: "g p", description: "Go to Projects" },
    { keys: "g s", description: "Go to Safety" },
    { keys: "g d", description: "Go to Documents" },
    { keys: "g r", description: "Go to RFIs" },
    { keys: "g a", description: "Go to Admin" },
    { keys: "/", description: "Focus search" },
    { keys: "Esc", description: "Close dialogs" },
  ];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
    >
      <div
        className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl dark:bg-gray-800"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-4 text-lg font-semibold text-gray-900 dark:text-white">
          Keyboard Shortcuts
        </h2>
        <div className="space-y-2">
          {shortcuts.map(({ keys, description }) => (
            <div key={keys} className="flex items-center justify-between py-1">
              <span className="text-sm text-gray-600 dark:text-gray-300">{description}</span>
              <kbd className="rounded bg-gray-100 px-2 py-0.5 font-mono text-xs text-gray-800 dark:bg-gray-700 dark:text-gray-200">
                {keys}
              </kbd>
            </div>
          ))}
        </div>
        <button
          onClick={onClose}
          className="mt-4 w-full rounded bg-gray-100 px-3 py-2 text-sm text-gray-700 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:hover:bg-gray-600"
        >
          Close
        </button>
      </div>
    </div>
  );
}

export function KeyboardShortcutsProvider({ children }: { children: React.ReactNode }) {
  const [showHelp, setShowHelp] = useState(false);

  const handleToggleHelp = useCallback(() => {
    setShowHelp((prev) => !prev);
  }, []);

  useKeyboardShortcut("?", handleToggleHelp, { shift: true });

  useEffect(() => {
    function handleEsc(e: KeyboardEvent) {
      if (e.key === "Escape") setShowHelp(false);
    }
    window.addEventListener("keydown", handleEsc);
    return () => window.removeEventListener("keydown", handleEsc);
  }, []);

  return (
    <>
      {children}
      <KeyboardShortcutsHelp open={showHelp} onClose={() => setShowHelp(false)} />
    </>
  );
}
