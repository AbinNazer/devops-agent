import { create } from "zustand";
import { healthService } from "../services/healthService";
import { infraService } from "../services/infraService";
import type { InfraStatus } from "../types";

interface AppState {
  backendOnline: boolean;
  infra: InfraStatus | null;
  infraError: string | null;
  checkBackend: () => Promise<void>;
  loadInfra: () => Promise<void>;
}

export const useAppStore = create<AppState>((set) => ({
  backendOnline: false,
  infra: null,
  infraError: null,
  checkBackend: async () => {
    try {
      await healthService.check();
      set({ backendOnline: true });
    } catch {
      set({ backendOnline: false });
    }
  },
  loadInfra: async () => {
    try {
      set({ infraError: null });
      const infra = await infraService.status();
      set({ infra });
    } catch (error) {
      set({ infraError: error instanceof Error ? error.message : "Unable to load infrastructure" });
    }
  },
}));
