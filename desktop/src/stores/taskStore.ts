import { create } from "zustand";
import type { Task } from "../types";
import { taskService } from "../services/taskService";

interface TaskState {
  tasks: Task[];
  load: () => Promise<void>;
  cancel: (id: string) => Promise<void>;
}

export const useTaskStore = create<TaskState>((set, get) => ({
  tasks: [],

  load: async () => {
    try {
      const tasks = await taskService.list();
      set({ tasks });
    } catch {
      /* task polling failures shouldn't surface as a hard error in the UI */
    }
  },

  cancel: async (id: string) => {
    // optimistic — mark cancelled immediately, reconcile on next poll
    set({ tasks: get().tasks.map((t) => (t.id === id ? { ...t, status: "cancelled" } : t)) });
    try {
      await taskService.cancel(id);
    } catch {
      await get().load(); // reconcile with real state if the cancel call failed
    }
  },
}));