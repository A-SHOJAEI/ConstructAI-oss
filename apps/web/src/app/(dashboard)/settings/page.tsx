"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/hooks/use-auth";
import { apiClient } from "@/lib/api-client";
import { ProcoreConnection } from "@/components/settings/procore-connection";

interface NotificationPrefs {
  email_notifications: boolean;
  safety_alerts: boolean;
  schedule_changes: boolean;
  daily_digest: boolean;
}

function Toggle({
  enabled,
  onChange,
  disabled,
}: {
  enabled: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      disabled={disabled}
      onClick={() => onChange(!enabled)}
      className={`w-10 h-6 rounded-full relative transition-colors ${
        enabled ? "bg-blue-600" : "bg-gray-200 dark:bg-gray-700"
      } ${disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
    >
      <div
        className={`w-4 h-4 bg-white rounded-full absolute top-1 shadow transition-transform ${
          enabled ? "translate-x-5" : "translate-x-1"
        }`}
      />
    </button>
  );
}

export default function SettingsPage() {
  const { user, isLoading: authLoading } = useAuth();
  const queryClient = useQueryClient();
  const [editName, setEditName] = useState(false);
  const [nameValue, setNameValue] = useState("");

  const { data: prefs, isLoading: prefsLoading } = useQuery<NotificationPrefs>({
    queryKey: ["notification-prefs"],
    queryFn: () => apiClient.get<NotificationPrefs>("/api/v1/users/me/notification-preferences"),
  });

  const prefsMutation = useMutation({
    mutationFn: (body: NotificationPrefs) =>
      apiClient.patch<NotificationPrefs>("/api/v1/users/me/notification-preferences", body),
    onSuccess: (data) => {
      queryClient.setQueryData(["notification-prefs"], data);
    },
  });

  const nameMutation = useMutation({
    mutationFn: (full_name: string) => apiClient.patch("/api/v1/users/me", { full_name }),
    onSuccess: () => {
      setEditName(false);
      queryClient.invalidateQueries({ queryKey: ["notification-prefs"] });
    },
  });

  function togglePref(key: keyof NotificationPrefs) {
    if (!prefs) return;
    prefsMutation.mutate({ ...prefs, [key]: !prefs[key] });
  }

  return (
    <div className="p-6 max-w-4xl">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Settings</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Manage your account and application preferences
        </p>
      </div>

      {/* Profile Section */}
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6 mb-6">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">Profile</h2>

        {authLoading && (
          <div className="space-y-4">
            <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-1/3 animate-pulse" />
            <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-1/2 animate-pulse" />
            <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-1/4 animate-pulse" />
          </div>
        )}

        {!authLoading && !user && (
          <p className="text-gray-500 dark:text-gray-400 text-sm">
            Not authenticated. Please log in to view your profile.
          </p>
        )}

        {!authLoading && user && (
          <div className="space-y-4">
            <div className="flex items-center gap-4">
              <div className="w-16 h-16 bg-blue-600 rounded-full flex items-center justify-center text-white text-xl font-bold">
                {(user.full_name || user.email || "U").charAt(0).toUpperCase()}
              </div>
              <div>
                <p className="text-lg font-medium text-gray-900 dark:text-white">
                  {user.full_name || "No name set"}
                </p>
                <p className="text-sm text-gray-500 dark:text-gray-400">{user.email || "-"}</p>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-4 border-t border-gray-100 dark:border-gray-700">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">
                  Full Name
                </label>
                {editName ? (
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={nameValue}
                      onChange={(e) => setNameValue(e.target.value)}
                      className="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:text-white"
                    />
                    <button
                      onClick={() => nameMutation.mutate(nameValue)}
                      disabled={nameMutation.isPending}
                      className="px-3 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50"
                    >
                      Save
                    </button>
                    <button
                      onClick={() => setEditName(false)}
                      className="px-3 py-2 text-gray-600 dark:text-gray-300 text-sm"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      value={user.full_name || ""}
                      className="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-gray-50 dark:bg-gray-700 dark:text-white"
                      readOnly
                    />
                    <button
                      onClick={() => {
                        setNameValue(user.full_name || "");
                        setEditName(true);
                      }}
                      className="text-blue-600 text-sm hover:text-blue-800"
                    >
                      Edit
                    </button>
                  </div>
                )}
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">
                  Email
                </label>
                <input
                  type="email"
                  defaultValue={user.email || ""}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-gray-50 dark:bg-gray-700 dark:text-white"
                  readOnly
                />
              </div>
              {user.role && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">
                    Role
                  </label>
                  <input
                    type="text"
                    defaultValue={user.role}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-gray-50 dark:bg-gray-700 dark:text-white"
                    readOnly
                  />
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Integrations Section */}
      <div className="mb-6">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">Integrations</h2>
        <ProcoreConnection />
      </div>

      {/* Notifications Section */}
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6 mb-6">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">Notifications</h2>
        {prefsLoading ? (
          <div className="space-y-4">
            <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-1/2 animate-pulse" />
            <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-1/3 animate-pulse" />
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center justify-between py-2">
              <div>
                <p className="text-sm font-medium text-gray-900 dark:text-white">
                  Email Notifications
                </p>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  Receive updates about project changes via email
                </p>
              </div>
              <Toggle
                enabled={prefs?.email_notifications ?? false}
                onChange={() => togglePref("email_notifications")}
                disabled={prefsMutation.isPending}
              />
            </div>
            <div className="flex items-center justify-between py-2">
              <div>
                <p className="text-sm font-medium text-gray-900 dark:text-white">Safety Alerts</p>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  Get notified about safety incidents in real-time
                </p>
              </div>
              <Toggle
                enabled={prefs?.safety_alerts ?? true}
                onChange={() => togglePref("safety_alerts")}
                disabled={prefsMutation.isPending}
              />
            </div>
            <div className="flex items-center justify-between py-2">
              <div>
                <p className="text-sm font-medium text-gray-900 dark:text-white">
                  Schedule Changes
                </p>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  Notifications when schedule milestones change
                </p>
              </div>
              <Toggle
                enabled={prefs?.schedule_changes ?? false}
                onChange={() => togglePref("schedule_changes")}
                disabled={prefsMutation.isPending}
              />
            </div>
            <div className="flex items-center justify-between py-2">
              <div>
                <p className="text-sm font-medium text-gray-900 dark:text-white">Daily Digest</p>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  Receive a daily summary of all activity
                </p>
              </div>
              <Toggle
                enabled={prefs?.daily_digest ?? false}
                onChange={() => togglePref("daily_digest")}
                disabled={prefsMutation.isPending}
              />
            </div>
          </div>
        )}
      </div>

      {/* API Keys Section */}
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">API Keys</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
          Manage API keys for programmatic access to ConstructAI.
        </p>
        <div className="border border-dashed rounded-lg p-6 text-center text-muted-foreground">
          <h3 className="font-medium mb-2 text-gray-900 dark:text-white">API Key Management</h3>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            API key self-service is planned for the next release. Contact your administrator to
            manage API keys.
          </p>
        </div>
      </div>
    </div>
  );
}
