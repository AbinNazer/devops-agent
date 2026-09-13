import { api } from "./api";
import type { Provider } from "../types";

interface ProviderListResponse { providers: Provider[]; active_provider: string; }
export const providerService = {
  list: () => api.get<ProviderListResponse>("/providers"),
  switch: (providerId: string) => api.post<{ success: boolean; provider: string }>("/providers/switch", { provider: providerId }),
};
