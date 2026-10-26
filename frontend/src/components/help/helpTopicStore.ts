// Which help topic a "Help" link asked for; the Help screen scrolls to it.
import { create } from 'zustand';

export const useHelpTopic = create<{ topic: string | null; open: (topic: string) => void }>((set) => ({
  topic: null,
  open: (topic) => set({ topic }),
}));
