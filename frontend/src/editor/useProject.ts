import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import type {
  AspectFormat,
  ClipAsset,
  ClipProject,
  EditorLayer,
  ProjectApiResponse,
  SubtitleLine,
  SubtitlePresetId,
  TrimConfig,
} from './types';

const MAX_HISTORY = 50;
const AUTOSAVE_MS = 1500;
const BACKOFF_MS = [2000, 4000, 8000, 16000];

export interface ProjectHistoryState {
  past: ClipProject[];
  present: ClipProject;
  future: ClipProject[];
  dirty: boolean;
}

export type ProjectAction =
  | { type: 'RESET'; project: ClipProject }
  | { type: 'MARK_SAVED' }
  | { type: 'UNDO' }
  | { type: 'REDO' }
  | { type: 'SET_TRIM'; trim: TrimConfig }
  | { type: 'ADD_CUT'; cut: [number, number] }
  | { type: 'REMOVE_CUT'; index: number }
  | { type: 'ADD_LAYER'; layer: EditorLayer }
  | { type: 'UPDATE_LAYER'; id: string; patch: Partial<EditorLayer> }
  | { type: 'REMOVE_LAYER'; id: string }
  | { type: 'REORDER_LAYER'; id: string; direction: 'up' | 'down' }
  | {
      type: 'SET_SUBTITLES_META';
      patch: {
        enabled?: boolean;
        preset?: SubtitlePresetId;
        position_y?: number;
      };
    }
  | { type: 'SET_SUBTITLE_LINES'; lines: SubtitleLine[] };

export function createHistoryState(project: ClipProject): ProjectHistoryState {
  return { past: [], present: project, future: [], dirty: false };
}

function pushPresent(
  state: ProjectHistoryState,
  next: ClipProject,
): ProjectHistoryState {
  const past = [...state.past, state.present].slice(-MAX_HISTORY);
  return { past, present: next, future: [], dirty: true };
}

export function projectReducer(
  state: ProjectHistoryState,
  action: ProjectAction,
): ProjectHistoryState {
  switch (action.type) {
    case 'RESET':
      return { past: [], present: action.project, future: [], dirty: false };
    case 'MARK_SAVED':
      return { ...state, dirty: false };
    case 'UNDO': {
      if (state.past.length === 0) return state;
      const previous = state.past[state.past.length - 1];
      return {
        past: state.past.slice(0, -1),
        present: previous,
        future: [state.present, ...state.future].slice(0, MAX_HISTORY),
        dirty: true,
      };
    }
    case 'REDO': {
      if (state.future.length === 0) return state;
      const [next, ...rest] = state.future;
      return {
        past: [...state.past, state.present].slice(-MAX_HISTORY),
        present: next,
        future: rest,
        dirty: true,
      };
    }
    case 'SET_TRIM':
      return pushPresent(state, { ...state.present, trim: action.trim });
    case 'ADD_CUT': {
      const [a, b] = action.cut;
      const clampedA = Math.max(state.present.trim.in, a);
      const clampedB = Math.min(state.present.trim.out, b);
      if (clampedB - clampedA < 0.08) return state;
      const raw = [...state.present.trim.cuts, [clampedA, clampedB] as [number, number]].sort(
        (x, y) => x[0] - y[0],
      );
      const merged: [number, number][] = [];
      for (const item of raw) {
        const prev = merged[merged.length - 1];
        if (prev && item[0] <= prev[1] + 1e-4) {
          prev[1] = Math.max(prev[1], item[1]);
        } else {
          merged.push([
            Math.round(item[0] * 1000) / 1000,
            Math.round(item[1] * 1000) / 1000,
          ]);
        }
      }
      return pushPresent(state, {
        ...state.present,
        trim: { ...state.present.trim, cuts: merged },
      });
    }
    case 'REMOVE_CUT':
      return pushPresent(state, {
        ...state.present,
        trim: {
          ...state.present.trim,
          cuts: state.present.trim.cuts.filter((_, idx) => idx !== action.index),
        },
      });
    case 'ADD_LAYER':
      return pushPresent(state, {
        ...state.present,
        layers: [...state.present.layers, action.layer],
      });
    case 'UPDATE_LAYER':
      return pushPresent(state, {
        ...state.present,
        layers: state.present.layers.map((l) =>
          l.id === action.id ? ({ ...l, ...action.patch } as EditorLayer) : l,
        ),
      });
    case 'REMOVE_LAYER':
      return pushPresent(state, {
        ...state.present,
        layers: state.present.layers.filter((l) => l.id !== action.id),
      });
    case 'REORDER_LAYER': {
      const idx = state.present.layers.findIndex((l) => l.id === action.id);
      if (idx < 0) return state;
      const target = action.direction === 'up' ? idx + 1 : idx - 1;
      if (target < 0 || target >= state.present.layers.length) return state;
      const copy = [...state.present.layers];
      const [picked] = copy.splice(idx, 1);
      copy.splice(target, 0, picked);
      return pushPresent(state, { ...state.present, layers: copy });
    }
    case 'SET_SUBTITLES_META':
      return pushPresent(state, {
        ...state.present,
        subtitles: { ...state.present.subtitles, ...action.patch },
      });
    case 'SET_SUBTITLE_LINES':
      return pushPresent(state, {
        ...state.present,
        subtitles: { ...state.present.subtitles, lines: action.lines },
      });
    default:
      return state;
  }
}

const EMPTY_PROJECT: ClipProject = {
  version: 1,
  trim: { in: 0, out: 10, cuts: [] },
  subtitles: { enabled: true, preset: 'tiktok_viral', position_y: 0.78, lines: [] },
  layers: [],
};

export type SaveStatus = 'loading' | 'saved' | 'saving' | 'unsaved' | 'conflict';

export function useProject(clipId: string) {
  const [history, dispatch] = useReducer(
    projectReducer,
    EMPTY_PROJECT,
    createHistoryState,
  );
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [revision, setRevision] = useState(1);
  const [masterUrl, setMasterUrl] = useState('');
  const [masterDuration, setMasterDuration] = useState(10);
  const [format, setFormat] = useState<AspectFormat>('9:16');
  const [limited, setLimited] = useState(false);
  const [hookText, setHookText] = useState('');
  const [assets, setAssets] = useState<ClipAsset[]>([]);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('loading');
  const [saveError, setSaveError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);

  const revRef = useRef(1);
  const retryIdxRef = useRef(0);
  const timerRef = useRef<number | null>(null);
  const presentRef = useRef(history.present);

  useEffect(() => {
    revRef.current = revision;
  }, [revision]);

  useEffect(() => {
    presentRef.current = history.present;
  }, [history.present]);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    setConflict(false);
    try {
      const r = await fetch(`/api/clips/${encodeURIComponent(clipId)}/project`);
      if (!r.ok) {
        throw new Error(`Gagal memuat proyek (${r.status})`);
      }
      const data = (await r.json()) as ProjectApiResponse;
      dispatch({ type: 'RESET', project: data.project });
      setRevision(data.revision);
      revRef.current = data.revision;
      setMasterUrl(data.masterUrl);
      setMasterDuration(data.masterDuration);
      setFormat(data.format);
      setLimited(data.limited);
      setHookText(data.hookText || '');
      setAssets(data.assets || []);
      setSaveStatus('saved');
      setSaveError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Gagal memuat editor.');
    } finally {
      setLoading(false);
    }
  }, [clipId]);

  useEffect(() => {
    void load();
  }, [load]);

  const performSave = useCallback(
    async (proj: ClipProject, force = false): Promise<boolean> => {
      setSaveStatus('saving');
      setSaveError(null);
      try {
        const r = await fetch(
          `/api/clips/${encodeURIComponent(clipId)}/project`,
          {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              project: proj,
              revision: revRef.current,
              force,
            }),
          },
        );
        if (r.status === 409) {
          setConflict(true);
          setSaveStatus('conflict');
          return false;
        }
        if (r.status === 422) {
          const body = await r.json().catch(() => ({ detail: 'Validasi gagal' }));
          setSaveStatus('unsaved');
          setSaveError(String(body.detail || 'Validasi gagal'));
          return false;
        }
        if (!r.ok) {
          throw new Error(`HTTP ${r.status}`);
        }
        const saved = (await r.json()) as { revision: number };
        revRef.current = saved.revision;
        setRevision(saved.revision);
        retryIdxRef.current = 0;
        setConflict(false);
        dispatch({ type: 'MARK_SAVED' });
        setSaveStatus('saved');
        return true;
      } catch (err) {
        setSaveStatus('unsaved');
        setSaveError(
          err instanceof Error ? err.message : 'Gagal menyimpan perubahan',
        );
        const delay =
          BACKOFF_MS[Math.min(retryIdxRef.current, BACKOFF_MS.length - 1)];
        retryIdxRef.current += 1;
        timerRef.current = window.setTimeout(() => {
          void performSave(presentRef.current, false);
        }, delay);
        return false;
      }
    },
    [clipId],
  );

  // Debounced autosave whenever project becomes dirty
  useEffect(() => {
    if (loading || !history.dirty || conflict) return;
    setSaveStatus('saving');
    if (timerRef.current) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      void performSave(history.present, false);
    }, AUTOSAVE_MS);
    return () => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, [history.present, history.dirty, loading, conflict, performSave]);

  // Warn before closing tab if unsaved
  useEffect(() => {
    if (!history.dirty && saveStatus !== 'unsaved') return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [history.dirty, saveStatus]);

  const flushSave = useCallback(
    async (overrideProject?: ClipProject): Promise<boolean> => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
      return performSave(overrideProject ?? presentRef.current, false);
    },
    [performSave],
  );

  const forceOverwrite = useCallback(async () => {
    if (timerRef.current) window.clearTimeout(timerRef.current);
    await performSave(presentRef.current, true);
  }, [performSave]);

  return {
    loading,
    error: loadError,
    project: history.present,
    revision,
    masterUrl,
    masterDuration,
    format,
    limited,
    hookText,
    assets,
    setAssets,
    saveStatus,
    saveError,
    conflict,
    dispatch,
    canUndo: history.past.length > 0,
    canRedo: history.future.length > 0,
    undo: () => dispatch({ type: 'UNDO' }),
    redo: () => dispatch({ type: 'REDO' }),
    reload: load,
    forceOverwrite,
    flushSave,
  };
}
