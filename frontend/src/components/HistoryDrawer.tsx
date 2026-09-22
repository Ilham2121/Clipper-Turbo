import React, { useEffect } from 'react';
import type { HistoryEntry } from '../history';

interface HistoryDrawerProps {
  open: boolean;
  entries: HistoryEntry[];
  onSelect: (entry: HistoryEntry) => void;
  onRemove: (jobId: string) => void;
  onClear: () => void;
  onClose: () => void;
}

export const HistoryDrawer: React.FC<HistoryDrawerProps> = ({
  open,
  entries,
  onSelect,
  onRemove,
  onClear,
  onClose,
}) => {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="history-overlay" onClick={onClose}>
      <aside className="history-panel" onClick={(e) => e.stopPropagation()}>
        <div className="history-header">
          <h2 className="card-title" style={{ marginBottom: 0 }}>Riwayat Klip</h2>
          <button type="button" className="btn-secondary" onClick={onClose}>
            Tutup
          </button>
        </div>
        {entries.length === 0 ? (
          <p className="card-description">Belum ada riwayat. Klip yang selesai akan tersimpan di sini.</p>
        ) : (
          <>
            <button type="button" className="btn-secondary btn-danger" onClick={onClear} style={{ marginBottom: '1rem' }}>
              Hapus Semua
            </button>
            <ul className="history-list">
              {entries.map((entry) => (
                <li key={entry.jobId} className="history-item">
                  <button type="button" className="history-select" onClick={() => onSelect(entry)}>
                    <span className="history-url" title={entry.youtubeUrl}>{entry.youtubeUrl}</span>
                    <span className="history-meta">
                      {new Date(entry.createdAt).toLocaleString()} · {entry.clips.length} klip · {entry.status}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="btn-secondary btn-danger"
                    onClick={() => onRemove(entry.jobId)}
                    title="Hapus entri"
                  >
                    Hapus
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </aside>
    </div>
  );
};
