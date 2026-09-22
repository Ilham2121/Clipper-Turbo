import type { ClipInfo } from './api/client';

export interface HistoryEntry {
  jobId: string;
  youtubeUrl: string;
  createdAt: string;
  status: string;
  clips: ClipInfo[];
}

const STORAGE_KEY = 'clipper-turbo:job-history:v1';
const MAX_ENTRIES = 20;

export function loadHistory(): HistoryEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (e): e is HistoryEntry =>
        typeof e === 'object' && e !== null && typeof e.jobId === 'string' && Array.isArray(e.clips),
    );
  } catch {
    return [];
  }
}

function persist(entries: HistoryEntry[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(0, MAX_ENTRIES)));
  } catch {
    // ponytail: quota-full/private-mode writes are best-effort; add when eviction-by-size needed.
  }
}

export function upsertEntry(entry: HistoryEntry): HistoryEntry[] {
  const entries = [entry, ...loadHistory().filter((e) => e.jobId !== entry.jobId)];
  persist(entries);
  return entries;
}

export function removeEntry(jobId: string): HistoryEntry[] {
  const entries = loadHistory().filter((e) => e.jobId !== jobId);
  persist(entries);
  return entries;
}

export function clearHistory(): HistoryEntry[] {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
  return [];
}
