import { api } from "./api";
import type { InfraStatus } from "../types";

export const infraService = {
  async status(): Promise<InfraStatus> {
    const response = await api.get<{ status: string; data?: { result?: Record<string, unknown> } }>("/infra/status");
    const raw = response.data?.result ?? {};
    const sections: InfraStatus["sections"] = {};
    for (const [name, value] of Object.entries(raw)) {
      const values = Array.isArray(value) ? value : [value];
      sections[name] = values.map((item, index) => ({ label: typeof item === "string" ? item : `Check ${index + 1}`, status: response.status === "ok" ? "ok" : "unknown" }));
    }
    return { sections, overall: response.status === "ok" ? "ok" : "unknown" };
  },
};
