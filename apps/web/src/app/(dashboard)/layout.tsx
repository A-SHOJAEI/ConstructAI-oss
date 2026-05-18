"use client";

import { MainLayout } from "@/components/layout/main-layout";

// M-41: Historically this layout called queryClient.invalidateQueries() on
// every project change, which cascaded refetches of unrelated settings /
// billing / profile queries. Any hook that cares about the current project
// includes projectId in its queryKey — React Query auto-refetches when the
// key changes, so no global invalidation is needed here.
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return <MainLayout>{children}</MainLayout>;
}
