"use client";

import { useState, useEffect, useCallback } from "react";

interface TourStep {
  target: string;
  title: string;
  content: string;
  position?: "top" | "bottom" | "left" | "right";
}

const ONBOARDING_STEPS: TourStep[] = [
  {
    target: "[data-tour='project-selector']",
    title: "Select a Project",
    content:
      "Start by selecting a project from the dropdown. All data is scoped to the active project.",
    position: "bottom",
  },
  {
    target: "[data-tour='sidebar-safety']",
    title: "Safety Dashboard",
    content:
      "Monitor real-time safety alerts, PPE compliance, and zone violations from the Safety section.",
    position: "right",
  },
  {
    target: "[data-tour='sidebar-documents']",
    title: "Document Management",
    content:
      "Upload, search, and compare project documents. AI-powered classification and Q&A included.",
    position: "right",
  },
  {
    target: "[data-tour='sidebar-scheduling']",
    title: "Schedule Management",
    content: "Import schedules from P6/MS Project, run CPM analysis, and predict potential delays.",
    position: "right",
  },
  {
    target: "[data-tour='theme-toggle']",
    title: "Dark Mode",
    content: "Toggle between light, dark, and system theme modes.",
    position: "bottom",
  },
];

const TOUR_STORAGE_KEY = "constructai_onboarding_complete";

export function OnboardingTour() {
  const [currentStep, setCurrentStep] = useState(-1);
  const [isActive, setIsActive] = useState(false);

  useEffect(() => {
    const completed = localStorage.getItem(TOUR_STORAGE_KEY);
    if (!completed) {
      const timer = setTimeout(() => {
        setIsActive(true);
        setCurrentStep(0);
      }, 1500);
      return () => clearTimeout(timer);
    }
  }, []);

  const handleComplete = useCallback(() => {
    setIsActive(false);
    setCurrentStep(-1);
    localStorage.setItem(TOUR_STORAGE_KEY, "true");
  }, []);

  const handleNext = useCallback(() => {
    if (currentStep < ONBOARDING_STEPS.length - 1) {
      setCurrentStep((s) => s + 1);
    } else {
      handleComplete();
    }
  }, [currentStep, handleComplete]);

  const handleSkip = useCallback(() => {
    handleComplete();
  }, [handleComplete]);

  if (!isActive || currentStep < 0) return null;

  const step = ONBOARDING_STEPS[currentStep];
  const isLast = currentStep === ONBOARDING_STEPS.length - 1;

  return (
    <>
      {/* Overlay */}
      <div className="fixed inset-0 z-40 bg-black/30" />

      {/* Tooltip */}
      <div
        className="fixed z-50 w-80 rounded-lg bg-white p-4 shadow-xl dark:bg-gray-800"
        style={{ top: "50%", left: "50%", transform: "translate(-50%, -50%)" }}
        role="dialog"
        aria-modal="true"
        aria-label={`Tour step ${currentStep + 1} of ${ONBOARDING_STEPS.length}`}
      >
        <div className="mb-1 text-xs text-gray-400">
          Step {currentStep + 1} of {ONBOARDING_STEPS.length}
        </div>
        <h3 className="text-base font-semibold text-gray-900 dark:text-white">{step.title}</h3>
        <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">{step.content}</p>
        <div className="mt-4 flex items-center justify-between">
          <button
            onClick={handleSkip}
            className="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
          >
            Skip tour
          </button>
          <div className="flex gap-2">
            {currentStep > 0 && (
              <button
                onClick={() => setCurrentStep((s) => s - 1)}
                className="rounded bg-gray-100 px-3 py-1 text-sm dark:bg-gray-700 dark:text-gray-300"
              >
                Back
              </button>
            )}
            <button
              onClick={handleNext}
              className="rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700"
            >
              {isLast ? "Done" : "Next"}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

export function useStartTour() {
  return useCallback(() => {
    localStorage.removeItem(TOUR_STORAGE_KEY);
    window.location.reload();
  }, []);
}
