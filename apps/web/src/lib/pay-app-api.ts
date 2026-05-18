import { apiClient } from "./api-client";

export interface PayApplication {
  id: string;
  project_id: string;
  application_number: number;
  period_to: string;
  original_contract_sum: number;
  net_change_by_cos: number;
  contract_sum_to_date: number;
  total_completed_and_stored: number;
  retainage_pct: number;
  total_retainage: number;
  total_earned_less_retainage: number;
  less_previous_certificates: number;
  current_payment_due: number;
  balance_to_finish_including_retainage: number;
  status: string;
  submitted_at: string | null;
  certified_at: string | null;
  paid_at: string | null;
  created_at: string;
}

export interface SOVLineItem {
  id: string;
  project_id: string;
  item_number: string;
  description: string;
  scheduled_value: number;
  csi_code: string | null;
  is_change_order_line: boolean;
  sort_order: number;
}

export const payAppApi = {
  list: (projectId: string) =>
    apiClient.get<{ data: PayApplication[]; meta: { cursor: string | null; has_more: boolean } }>(
      `/api/v1/pay-applications?project_id=${projectId}&limit=100`,
    ),

  get: (id: string) => apiClient.get<PayApplication>(`/api/v1/pay-applications/${id}`),

  sov: (projectId: string) =>
    apiClient.get<{ data: SOVLineItem[]; meta: { cursor: string | null; has_more: boolean } }>(
      `/api/v1/pay-applications/sov?project_id=${projectId}&limit=200`,
    ),

  downloadG702: (id: string) =>
    `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/v1/pay-applications/${id}/pdf/g702`,

  downloadG703: (id: string) =>
    `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/v1/pay-applications/${id}/pdf/g703`,
};
