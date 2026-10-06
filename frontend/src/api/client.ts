export type AspectRatio = '9:16' | '16:9';

export interface ManualSegment {
  start: number;
  end: number;
}

export interface JobError {
  stage: string;
  message: string;
}

export interface ClipInfo {
  id: string;
  segmentStart: number;
  segmentEnd: number;
  format: AspectRatio;
  downloadUrl: string;
  subtitleUrl?: string | null;
  viralityScore?: number | null;
  hook?: string | null;
  caption?: string | null;
  hashtags?: string[] | null;
}

export function buildCaptionText(clip: ClipInfo): string {
  const parts: string[] = [];
  if (clip.caption) parts.push(clip.caption);
  const tags = (clip.hashtags ?? []).filter(Boolean);
  if (tags.length > 0) parts.push(tags.join(' '));
  return parts.join('\n');
}

export async function copyCaption(clip: ClipInfo): Promise<boolean> {
  const text = buildCaptionText(clip);
  if (!text) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      return true;
    } catch {
      return false;
    }
  }
}

export interface ProgressLogEntry {
  t: string;
  msg: string;
}

export interface JobStatusResponse {
  jobId: string;
  status: 'queued' | 'downloading' | 'transcribing' | 'selecting_segments' | 'rendering' | 'completed' | 'failed';
  progressPercent?: number;
  error?: JobError | null;
  clips?: ClipInfo[] | null;
  warning?: string | null;
  progressLog?: ProgressLogEntry[] | null;
}

const LOG_VISIBILITY_KEY = 'clipper-turbo:progress-log-visible';

export function loadLogVisible(): boolean {
  try {
    const raw = localStorage.getItem(LOG_VISIBILITY_KEY);
    return raw === null ? true : raw === '1';
  } catch {
    return true;
  }
}

export function saveLogVisible(visible: boolean): void {
  try {
    localStorage.setItem(LOG_VISIBILITY_KEY, visible ? '1' : '0');
  } catch {
    // ignore
  }
}

export interface CreateJobResponse {
  jobId: string;
  status: 'queued';
}

export interface CreateJobFormData {
  youtubeUrl: string;
  selectionMode: 'auto' | 'manual';
  outputFormats: AspectRatio[];
  burnSubtitles?: boolean;
  manualSegments?: ManualSegment[];
  llmProvider?: string;
  llmApiKey?: string;
  llmModel?: string;
  llmBaseUrl?: string;
}

export const LLM_PRESETS: Record<string, { label: string; defaultModel: string; endpoint: string; freeTier: boolean; tip: string }> = {
  openrouter: {
    label: 'OpenRouter (Free Models Available)',
    defaultModel: 'meta-llama/llama-3.3-70b-instruct:free',
    endpoint: 'https://openrouter.ai/api/v1',
    freeTier: true,
    tip: 'Bisa gunakan model gratis seperti Llama 3.3 70B atau DeepSeek R1',
  },
  groq: {
    label: 'Groq Cloud (Free Tier - Ultra Fast)',
    defaultModel: 'llama-3.3-70b-versatile',
    endpoint: 'https://api.groq.com/openai/v1',
    freeTier: true,
    tip: 'Gratis quota harian tinggi & eksekusi instan',
  },
  nvidia: {
    label: 'NVIDIA NIM (Free 1000 Credits)',
    defaultModel: 'meta/llama-3.3-70b-instruct',
    endpoint: 'https://integrate.api.nvidia.com/v1',
    freeTier: true,
    tip: 'Free tier 1000 credits untuk developer',
  },
  deepseek: {
    label: 'DeepSeek API',
    defaultModel: 'deepseek-chat',
    endpoint: 'https://api.deepseek.com/v1',
    freeTier: false,
    tip: 'Sangat terjangkau dengan performa penalaran tinggi',
  },
  openai: {
    label: 'OpenAI (Official)',
    defaultModel: 'gpt-4o-mini',
    endpoint: 'https://api.openai.com/v1',
    freeTier: false,
    tip: 'Model standar gpt-4o-mini',
  },
  custom: {
    label: 'Custom OpenAI-Compatible Endpoint',
    defaultModel: '',
    endpoint: '',
    freeTier: false,
    tip: 'Self-hosted (Ollama / vLLM / LM Studio)',
  },
};

/**
 * Builds the exact JSON payload following AC-11 (field exclusivity).
 */
export function buildJobPayload(formData: CreateJobFormData): Record<string, unknown> {
  const base: Record<string, unknown> = {
    youtubeUrl: formData.youtubeUrl,
    selectionMode: formData.selectionMode,
    outputFormats: formData.outputFormats,
    burnSubtitles: formData.burnSubtitles ?? true,
  };

  if (formData.selectionMode === 'manual') {
    base.manualSegments = formData.manualSegments || [];
  } else if (formData.selectionMode === 'auto') {
    base.llmProvider = formData.llmProvider || 'openai';
    if (formData.llmApiKey?.trim()) base.llmApiKey = formData.llmApiKey.trim();
    if (formData.llmModel?.trim()) base.llmModel = formData.llmModel.trim();
    if (formData.llmBaseUrl?.trim()) base.llmBaseUrl = formData.llmBaseUrl.trim();
  }

  return base;
}

export interface LlmKeyStatus {
  provider: string;
  hasKey: boolean;
  maskedKey?: string | null;
  model?: string | null;
  baseUrl?: string | null;
  updatedAt?: string | null;
  lastVerifiedAt?: string | null;
  lastVerifiedOk?: boolean | null;
}

export interface LlmTestResult {
  ok: boolean;
  message: string;
}

async function parseError(response: globalThis.Response, fallback: string): Promise<string> {
  try {
    const errJson = await response.json();
    if (errJson.detail) {
      if (Array.isArray(errJson.detail)) {
        return errJson.detail.map((d: { msg?: string }) => d.msg || JSON.stringify(d)).join(', ');
      }
      return typeof errJson.detail === 'string' ? errJson.detail : JSON.stringify(errJson.detail);
    }
  } catch {
    return `HTTP ${response.status}: ${response.statusText}`;
  }
  return fallback;
}

export async function listLlmKeys(): Promise<LlmKeyStatus[]> {
  const response = await fetch('/api/llm-keys');
  if (!response.ok) throw new Error(await parseError(response, 'Gagal memuat status API key'));
  return response.json();
}

export async function saveLlmKey(
  provider: string,
  payload: { apiKey: string; model?: string; baseUrl?: string },
): Promise<LlmKeyStatus> {
  const response = await fetch(`/api/llm-keys/${encodeURIComponent(provider)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await parseError(response, 'Gagal menyimpan API key'));
  return response.json();
}

export async function deleteLlmKey(provider: string): Promise<void> {
  const response = await fetch(`/api/llm-keys/${encodeURIComponent(provider)}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error(await parseError(response, 'Gagal menghapus API key'));
}

export async function testLlmKey(
  provider: string,
  payload?: { apiKey?: string; model?: string; baseUrl?: string },
): Promise<LlmTestResult> {
  const response = await fetch(`/api/llm-keys/${encodeURIComponent(provider)}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload ?? {}),
  });
  if (!response.ok) throw new Error(await parseError(response, 'Tes koneksi gagal'));
  return response.json();
}

export interface StorageCategory {
  category: string;
  label: string;
  bytes: number;
  files: number;
}

export interface StorageReport {
  totalBytes: number;
  purgeableBytes: number;
  retainedBytes: number;
  jobCount: number;
  fileCount: number;
  ttlHours: number;
  categories: StorageCategory[];
}

export interface PurgeResult {
  category: string;
  freedBytes: number;
  removedFiles: number;
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / Math.pow(1024, i);
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export async function getStorageReport(): Promise<StorageReport> {
  const response = await fetch('/api/maintenance/storage');
  if (!response.ok) throw new Error(await parseError(response, 'Gagal memuat metrik storage'));
  return response.json();
}

export async function purgeStorage(category: string): Promise<PurgeResult> {
  const response = await fetch(`/api/maintenance/purge/${encodeURIComponent(category)}`, {
    method: 'POST',
  });
  if (!response.ok) throw new Error(await parseError(response, 'Gagal membersihkan storage'));
  return response.json();
}

export async function revealStorage(
  scope: 'root' | 'jobs' | 'clips' | 'raw',
  jobId?: string,
): Promise<{ opened: boolean; scope: string }> {
  const response = await fetch('/api/maintenance/reveal', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scope, jobId }),
  });
  if (!response.ok) throw new Error(await parseError(response, 'Gagal membuka folder'));
  return response.json();
}

export interface SubtitleStyle {
  max_words: number;
  font_size: number;
  highlight_color: string;
  position: 'bottom' | 'center' | 'top';
  sync_mode?: 'block' | 'word';
}

export async function getSubtitleStyle(): Promise<SubtitleStyle> {
  const response = await fetch('/api/maintenance/settings/subtitle');
  if (!response.ok) throw new Error(await parseError(response, 'Gagal memuat gaya subtitle'));
  return response.json();
}

export async function saveSubtitleStyle(style: Partial<SubtitleStyle>): Promise<SubtitleStyle> {
  const response = await fetch('/api/maintenance/settings/subtitle', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(style),
  });
  if (!response.ok) throw new Error(await parseError(response, 'Gagal menyimpan gaya subtitle'));
  return response.json();
}

export interface AsrSettings {
  provider: string;
  model: string;
  device: string;
  language: string;
  glossary: string[];
  vad_filter: boolean;
}

export async function getAsrSettings(): Promise<AsrSettings> {
  const response = await fetch('/api/maintenance/settings/asr');
  if (!response.ok) throw new Error(await parseError(response, 'Gagal memuat konfigurasi Speech-to-Text'));
  return response.json();
}

export async function saveAsrSettings(settings: Partial<AsrSettings>): Promise<AsrSettings> {
  const response = await fetch('/api/maintenance/settings/asr', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
  if (!response.ok) throw new Error(await parseError(response, 'Gagal menyimpan konfigurasi Speech-to-Text'));
  return response.json();
}

export interface SystemDiagnostics {
  os: string;
  python: string;
  ffmpegInstalled: boolean;
  ffmpegPath: string;
  cudaAvailable: boolean;
  cudaDeviceCount: number;
  defaultDevice: string;
  activeJobs: number;
  maxConcurrent: number;
  dbPath: string;
}

export async function getSystemDiagnostics(): Promise<SystemDiagnostics> {
  const response = await fetch('/api/maintenance/diagnostics/system');
  if (!response.ok) throw new Error(await parseError(response, 'Gagal memuat data diagnostik'));
  return response.json();
}

export async function createJob(formData: CreateJobFormData): Promise<CreateJobResponse> {
  const payload = buildJobPayload(formData);

  const response = await fetch('/api/jobs', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    let errorDetail = 'Gagal membuat job pemrosesan klip';
    try {
      const errJson = await response.json();
      if (errJson.detail) {
        if (Array.isArray(errJson.detail)) {
          errorDetail = errJson.detail.map((d: { msg?: string }) => d.msg || JSON.stringify(d)).join(', ');
        } else {
          errorDetail = typeof errJson.detail === 'string' ? errJson.detail : JSON.stringify(errJson.detail);
        }
      }
    } catch {
      errorDetail = `HTTP ${response.status}: ${response.statusText}`;
    }
    throw new Error(errorDetail);
  }

  return response.json();
}

export interface EstimateBreakdown {
  download: number;
  transcribe: number;
  render: number;
}

export interface EstimateResponse {
  title: string;
  duration: number;
  estimatedSeconds: number;
  hardwareAccelerated: boolean;
  breakdown: EstimateBreakdown;
}

export function formatEstimate(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds));
  if (s < 60) return `±${s} detik`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r === 0 ? `±${m} menit` : `±${m} mnt ${r} dtk`;
}

export async function getEstimate(youtubeUrl: string): Promise<EstimateResponse | null> {
  try {
    const response = await fetch(`/api/estimate?youtubeUrl=${encodeURIComponent(youtubeUrl)}`);
    if (!response.ok) return null;
    return response.json();
  } catch {
    return null;
  }
}

export async function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
  if (!response.ok) {
    throw new Error(`Gagal mengambil status job: HTTP ${response.status}`);
  }
  return response.json();
}

export function getClipDownloadUrl(clipId: string): string {
  return `/api/clips/${encodeURIComponent(clipId)}`;
}

export function subscribeJobEvents(
  jobId: string,
  onStatus: (status: JobStatusResponse) => void,
): () => void {
  const source = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/stream`);
  source.onmessage = (event) => {
    try {
      onStatus(JSON.parse(event.data) as JobStatusResponse);
    } catch {
      // ignore malformed frames; polling fallback covers gaps
    }
  };
  source.onerror = () => {
    // Close on error; the 2s polling loop remains the fallback path.
    source.close();
  };
  return () => source.close();
}
