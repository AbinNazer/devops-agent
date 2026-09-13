import { create } from "zustand";
import type { Provider } from "../types";
import { providerService } from "../services/providerService";

interface ProviderState { providers: Provider[]; activeId: string | null; loading: boolean; error: string | null; load: () => Promise<void>; switchTo: (id: string) => Promise<void>; }
export const useProviderStore = create<ProviderState>((set) => ({
  providers: [], activeId: null, loading: false, error: null,
  load: async () => {
    set({ loading: true, error: null });
    try { const response = await providerService.list(); set({ providers: response.providers, activeId: response.providers.find((provider) => provider.name === response.active_provider)?.id ?? null, loading: false }); }
    catch (error) { set({ error: error instanceof Error ? error.message : "Unable to load providers", loading: false }); }
  },
  switchTo: async (id) => {
    const previous = useProviderStore.getState().activeId;
    set({ activeId: id });
    try { await providerService.switch(id); await useProviderStore.getState().load(); }
    catch (error) { set({ activeId: previous, error: error instanceof Error ? error.message : "Unable to switch provider" }); }
  },
}));
