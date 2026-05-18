// Shared type definitions between frontend and backend
export type ProjectStatus = "preconstruction" | "active" | "closeout" | "archived";
export type UserRole =
  | "platform_admin"
  | "owner_developer"
  | "general_contractor"
  | "project_manager"
  | "architect_engineer"
  | "subcontractor"
  | "inspector"
  | "safety_manager"
  | "read_only";
export type OrgType = "owner" | "gc" | "subcontractor" | "architect" | "engineer";
export type SubscriptionTier = "startup" | "growth" | "enterprise";
