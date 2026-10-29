import React, { useState } from 'react';
import { Image as ImageIcon, X } from 'lucide-react';
import { useToast } from '../components/Toast';
import { syncDirtyTitlePngs } from './syncTitlePngs';
import type { AspectFormat, ClipProject } from './types';
import type { ProjectAction } from './useProject';

interface ThumbnailDialogProps {
  clipId: string;
  project: ClipProject;
  format: AspectFormat;
  initialTime: number;
  flushSave: (overrideProject?: ClipProject) => Promise<boolean>;
  dispatch: React.Dispatch<ProjectAction>;
  onClose: () => void;
}

export const ThumbnailDialog: React.FC<ThumbnailDialogProps> = ({
  clipId,
  project,
  format,
  initialTime,
  flushSave,
  dispatch,
  onClose,
}) => {
  const toast = useToast();
  const [time, setTime] = useState(
    Math.max(project.trim.in, Math.min(project.trim.out, initialTime)),
  );
  const [includeTitles, setIncludeTitles] = useState(true);
  const [busy, setBusy] = useState(false);

  const handleDownload = async () => {
    setBusy(true);
    try {
      if (includeTitles) {
        const synced = await syncDirtyTitlePngs(clipId, project, format, dispatch);
        await flushSave(synced);
      }
      const r = await fetch(
        `/api/clips/${encodeURIComponent(clipId)}/thumbnail`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ time, includeTitles }),
        },
      );
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: 'Gagal membuat thumbnail' }));
        throw new Error(String(err.detail || 'Gagal membuat thumbnail'));
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `thumbnail-${clipId.slice(0, 8)}.png`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success('Thumbnail Diunduh', 'PNG 1080×1920 siap dipakai sebagai cover.');
      onClose();
    } catch (err) {
      toast.error(
        'Gagal Membuat Thumbnail',
        err instanceof Error ? err.message : 'Terjadi kesalahan.',
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-window"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 440, padding: '1.25rem', gap: '1rem' }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 className="modal-title" style={{ fontSize: '1rem' }}>
            Ekspor Thumbnail Cover (1080×1920)
          </h3>
          <button type="button" className="modal-close-btn" onClick={onClose}>
            <X size={15} />
          </button>
        </div>

        <div>
          <label className="form-label" style={{ fontSize: '0.78rem' }}>
            Pilih Waktu Frame: {time.toFixed(2)}s
          </label>
          <input
            type="range"
            min={project.trim.in}
            max={project.trim.out}
            step={0.05}
            value={time}
            onChange={(e) => setTime(Number(e.target.value))}
            style={{ width: '100%' }}
          />
        </div>

        <label
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.5rem',
            fontSize: '0.82rem',
          }}
        >
          <input
            type="checkbox"
            checked={includeTitles}
            onChange={(e) => setIncludeTitles(e.target.checked)}
          />
          <span>Sertakan layer Judul yang aktif pada waktu tersebut</span>
        </label>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem' }}>
          <button type="button" className="btn-secondary" onClick={onClose}>
            Batal
          </button>
          <button
            type="button"
            className="btn-primary"
            style={{ width: 'auto', padding: '0.45rem 1rem' }}
            disabled={busy}
            onClick={() => void handleDownload()}
          >
            <ImageIcon size={14} />
            <span>{busy ? 'Merender PNG...' : 'Unduh PNG'}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
