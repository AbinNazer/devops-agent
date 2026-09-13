/**
 * Health API service — backend health check.
 */
import { api } from './api';

interface HealthResponse {
  status: string;
  provider?: string;
}

export const healthService = {
  check: () => api.get<HealthResponse>('/health'),
  infra: () => api.get<{ status: string; cached: boolean; data?: Record<string, unknown> }>('/infra/status'),
};
