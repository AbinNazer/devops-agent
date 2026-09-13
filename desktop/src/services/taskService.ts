import { api } from "./api";
import type { Task } from "../types";
export const taskService = { list: async () => (await api.get<{ tasks: Task[] }>("/tasks")).tasks, cancel: (id: string) => api.post<void>(`/tasks/${id}/cancel`) };
