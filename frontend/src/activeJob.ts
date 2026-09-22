export interface ActiveJob {
  jobId: string;
  youtubeUrl: string;
}

const STORAGE_KEY = 'clipper-turbo:active-job:v1';

export function loadActiveJob(): ActiveJob | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (
      typeof parsed !== 'object' ||
      parsed === null ||
      typeof parsed.jobId !== 'string' ||
      !parsed.jobId
    ) {
      return null;
    }
    return {
      jobId: parsed.jobId,
      youtubeUrl: typeof parsed.youtubeUrl === 'string' ? parsed.youtubeUrl : '',
    };
  } catch {
    return null;
  }
}

export function saveActiveJob(job: ActiveJob): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(job));
  } catch {
    // ponytail: quota/private-mode writes best-effort; add when needed.
  }
}

export function clearActiveJob(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}
