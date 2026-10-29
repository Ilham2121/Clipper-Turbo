import React, { useCallback, useEffect, useState } from 'react';
import { Download, History, Loader2 } from 'lucide-react';
import { useToast } from '../components/Toast';
import { syncDirtyTitlePngs } from './syncTitlePngs';
import type { AspectFormat, ClipProject, VersionEntry } from './types';
import type { ProjectAction } from './useProject';

interface ExportStatusProps {
  clipId: string;
  project: ClipProject;
  format: AspectFormat;
  flushSave: (overrideProject?: ClipProject) => Promise<boolean>;
  dispatch: React.Dispatch<ProjectAction>;
  onClipUpdated: () => void;
}

export const ExportStatus: React.FC<ExportStatusProps> = ({
  clipId,
  project,
  format,
  flushSave,
  dispatch,
  onClipUpdated,
}) => {
  const toast = useToast();
  const [exporting, setExporting] = useState(false);
  const [progress, setProgress] = useState(0);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [versions, setVersions] = useState<VersionEntry[]>([]);

  const fetchVersions = useCallback(async () => {
    try {
      const r = await fetch(`/api/clips/${encodeURIComponent(clipId)}/versions`);
      if (r.ok) {
        const data = (await r.json()) as { versions: VersionEntry[] };
        setVersions(data.versions || []);
      }
    } catch {
      // ignore
    }
  }, [clipId]);

  useEffect(() => {
    void fetchVersions();
  }, [fetchVersions]);

  const handleExport = async () => {
    if (exporting) return;
    setExporting(true);
    setProgress(0.02);
    try {
      const synced = await syncDirtyTitlePngs(clipId, project, format, dispatch);
      const savedOk = await flushSave(synced);
      if (!savedOk) {
        throw new Error('Proyek belum tersimpan. Periksa pesan validasi/konflik.');
      }

      const r = await fetch(`/api/clips/${encodeURIComponent(clipId)}/export`, {
        method: 'POST',
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({ detail: 'Gagal memulai ekspor' }));
        throw new Error(String(body.detail || 'Gagal memulai ekspor'));
      }
      const { exportId } = (await r.json()) as { exportId: string };

      // Poll export progress
      let finished = false;
      while (!finished) {
        await new Promise((res) => window.setTimeout(res, 600));
        const stRes = await fetch(
          `/api/clips/${encodeURIComponent(clipId)}/export/${encodeURIComponent(exportId)}`,
        );
        if (!stRes.ok) throw new Error('Gagal membaca progres ekspor.');
        const st = (await stRes.json()) as {
          status: string;
          progress: number;
          error?: string | null;
          version?: number | null;
        };
        setProgress(st.progress ?? 0);
        if (st.status === 'done') {
          finished = true;
          toast.success(
            'Ekspor Selesai',
            `Versi v${st.version ?? ''} kini menjadi klip aktif.`,
          );
          await fetchVersions();
          onClipUpdated();
        } else if (st.status === 'failed') {
          throw new Error(st.error || 'FFmpeg gagal mengekspor klip.');
        }
      }
    } catch (err) {
      toast.error(
        'Ekspor Gagal',
        err instanceof Error ? err.message : 'Terjadi kesalahan saat ekspor.',
      );
    } finally {
      setExporting(false);
    }
  };

  const handleRestore = async (v: number) => {
    try {
      const r = await fetch(
        `/api/clips/${encodeURIComponent(clipId)}/versions/${v}/restore`,
        { method: 'POST' },
      );
      if (!r.ok) throw new Error('Gagal memulihkan versi');
      toast.success('Versi Dipulihkan', `Versi v${v} kembali aktif.`);
      setVersionsOpen(false);
      await fetchVersions();
      onClipUpdated();
    } catch (err) {
      toast.error(
        'Gagal Memulihkan',
        err instanceof Error ? err.message : 'Terjadi kesalahan.',
      );
    }
  };

  return (
    <div style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
      <button
        type="button"
        className="btn-secondary"
        style={{ padding: '0.4rem 0.65rem', fontSize: '0.78rem' }}
        onClick={() => {
          void fetchVersions();
          setVersionsOpen((o) => !o);
        }}
      >
        <History size={14} /> Versi ({versions.length})
      </button>

      <button
        type="button"
        className="btn-primary"
        disabled={exporting}
        onClick={() => void handleExport()}
        style={{
          width: 'auto',
          padding: '0.45rem 1rem',
          fontSize: '0.82rem',
          display: 'inline-flex',
          alignItems: 'center',
          gap: '0.4rem',
        }}
      >
        {exporting ? (
          <>
            <Loader2 size={14} /> Mengekspor {Math.round(progress * 100)}%
          </>
        ) : (
          <>
            <Download size={14} /> Ekspor MP4
          </>
        )}
      </button>

      {versionsOpen && (
        <div
          style={{
            position: 'absolute',
            right: 0,
            top: 'calc(100% + 6px)',
            width: 250,
            background: 'var(--surface-2)',
            border: '1px solid var(--border-strong)',
            borderRadius: 'var(--radius-md)',
            boxShadow: 'var(--shadow-deep)',
            padding: '0.5rem',
            zIndex: 80,
            display: 'flex',
            flexDirection: 'column',
            gap: '0.35rem',
          }}
        >
          <div style={{ fontSize: '0.74rem', fontWeight: 700, padding: '0.2rem 0.3rem' }}>
            Riwayat Versi (Asli + 3 Terakhir)
          </div>
          {versions.map((v) => (
            <div
              key={v.version}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '0.35rem 0.45rem',
                borderRadius: 'var(--radius-sm)',
                background: v.active ? 'var(--surface-3)' : 'transparent',
                fontSize: '0.76rem',
              }}
            >
              <div>
                <strong>{v.label}</strong>
                <div style={{ fontSize: '0.68rem', color: 'var(--fg-muted)' }}>
                  {v.durationSec}s {v.active ? '• Aktif' : ''}
                </div>
              </div>
              {!v.active && (
                <button
                  type="button"
                  className="btn-secondary"
                  style={{ padding: '0.2rem 0.45rem', fontSize: '0.7rem' }}
                  onClick={() => void handleRestore(v.version)}
                >
                  Pulihkan
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
