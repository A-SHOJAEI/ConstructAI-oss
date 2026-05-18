export interface User {
  id: string;
  email: string;
  full_name: string;
  role: string;
  org_id: string;
  is_active: boolean;
  created_at: string;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  type: string;
  subscription_tier: string;
  created_at: string;
}

export interface Project {
  id: string;
  name: string;
  project_number: string | null;
  type: string | null;
  status: string;
  address: string | null;
  contract_value: number | null;
  start_date: string | null;
  end_date: string | null;
  created_at: string;
}
