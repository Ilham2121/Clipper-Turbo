import React, { useCallback, useEffect, useState } from 'react';
import type { LlmKeyStatus, StorageReport, SubtitleStyle } from '../api/client';
import {
  deleteLlmKey,
  formatBytes,
  getStorageReport,
  getSubtitleStyle,
  listLlmKeys,
  purgeStorage,
  revealStorage,
  saveSubtitleStyle,
  testLlmKey,
} from '../api/client';
import type { HistoryEntry } from '../history';

type SettingsTab = 'history' | 'storage' | 'keys' | 'subtitle';

interface SettingsDrawerProps {
  open: boolean;
  entries: HistoryEntry[];
  onSelectHistory: (entry: HistoryEntry) => void;
  onRemoveHistory: (jobId: string) => void;
  onClearHistory: () => void;
  onClose: () => void;
}

const TABS: { key: SettingsTab; label: string }[] = [
  { key: 'history', label: 'Riwayat' },
  { key: 'storage', label: 'Storage' },
  { key: 'subtitle', label: 'Subtitle' },
  { key: 'keys', label: 'API Key' },
];

export const SettingsDrawer: React.FC<SettingsDrawerProps> = ({
  open,
  entries,
  onSelectHistory,
  onRemoveHistory,
  onClearHistory,
  onClose,
}) => {
  const [tab, setTab] = useState<SettingsTab>('history');

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
      <aside className="history-panel settings-panel" onClick={(e) => e.stopPropagation()}>
        <div className="history-header">
          <h2 className="card-title" style={{ marginBottom: 0 }}>Pengaturan</h2>
          <button type="button" className="btn-secondary" onClick={onClose}>
            Tutup
          </button>
        </div>

        <div className="settings-tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={tab === t.key}
              className={`settings-tab ${tab === t.key ? 'active' : ''}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>

        {tab === 'history' && (
          <HistoryTab
            entries={entries}
            onSelect={onSelectHistory}
            onRemove={onRemoveHistory}
            onClear={onClearHistory}
          />
        )}
        {tab === 'storage' && <StorageTab />}
        {tab === 'subtitle' && <SubtitleTab />}
        {tab === 'keys' && <KeysTab />}
      </aside>
    </div>
  );
};

// -- Riwayat ---------------------------------------------------------------

const HistoryTab: React.FC<{
  entries: HistoryEntry[];
  onSelect: (entry: HistoryEntry) => void;
  onRemove: (jobId: string) => void;
  onClear: () => void;
}> = ({ entries, onSelect, onRemove, onClear }) => {
  if (entries.length === 0) {
    return <p className="card-description">Belum ada riwayat. Klip yang selesai akan tersimpan di sini.</p>;
  }
  return (
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
  );
};

// -- Storage ---------------------------------------------------------------

const StorageTab: React.FC = () => {
  const [report, setReport] = useState<StorageReport | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setReport(await getStorageReport());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Gagal memuat metrik storage.');
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handlePurge = async (category: string) => {
    const label = report?.categories.find((c) => c.category === category)?.label ?? category;
    if (!window.confirm(`Hapus "${label}"? Tindakan ini permanen.`)) return;
    setBusy(category);
    setMessage(null);
    setError(null);
    try {
      const res = await purgeStorage(category);
      setMessage(`Dibersihkan: ${formatBytes(res.freedBytes)} dari ${res.removedFiles} file.`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Gagal membersihkan.');
    } finally {
      setBusy(null);
    }
  };

  const handleReveal = async (scope: 'root' | 'jobs') => {
    setError(null);
    try {
      await revealStorage(scope);
      setMessage('Folder dibuka di file manager.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Gagal membuka folder.');
    }
  };

  if (!report && !error) {
    return <p className="card-description">Memuat metrik storage...</p>;
  }

  return (
    <div className="settings-section">
      {error && <div className="banner banner-error"><span>{error}</span></div>}
      {message && <div className="banner banner-info"><span>{message}</span></div>}

      {report && (
        <>
          <div className="storage-summary">
            <div className="storage-metric">
              <span className="storage-metric-value">{formatBytes(report.purgeableBytes)}</span>
              <span className="storage-metric-label">Sampah dapat dibersihkan</span>
            </div>
            <div className="storage-metric">
              <span className="storage-metric-value">{formatBytes(report.retainedBytes)}</span>
              <span className="storage-metric-label">Klip aktif</span>
            </div>
            <div className="storage-metric">
              <span className="storage-metric-value">{report.jobCount}</span>
              <span className="storage-metric-label">Job · {report.fileCount} file</span>
            </div>
          </div>

          <button
            type="button"
            className="btn-primary btn-inline"
            style={{ width: '100%', marginBottom: '1rem' }}
            onClick={() => handlePurge('all')}
            disabled={busy !== null || report.purgeableBytes === 0}
          >
            {busy === 'all' ? 'Membersihkan...' : 'Bersihkan Semua Sampah'}
          </button>

          <ul className="storage-list">
            {report.categories.map((cat) => (
              <li key={cat.category} className="storage-row">
                <div className="storage-row-info">
                  <strong>{cat.label}</strong>
                  <span className="history-meta">{formatBytes(cat.bytes)} · {cat.files} file</span>
                </div>
                <button
                  type="button"
                  className="btn-secondary btn-danger"
                  onClick={() => handlePurge(cat.category)}
                  disabled={busy !== null || cat.files === 0}
                >
                  {busy === cat.category ? '...' : 'Bersihkan'}
                </button>
              </li>
            ))}
          </ul>

          <div className="storage-actions">
            <button type="button" className="btn-secondary" onClick={() => handleReveal('jobs')}>
              Buka Folder Jobs
            </button>
            <button type="button" className="btn-secondary" onClick={() => handleReveal('root')}>
              Buka Folder Storage
            </button>
            <button type="button" className="btn-secondary" onClick={() => void refresh()}>
              Segarkan
            </button>
          </div>

          <p className="form-helper">
            Klip aktif tidak pernah dihapus oleh pembersihan. TTL job: {report.ttlHours} jam.
          </p>
        </>
      )}
    </div>
  );
};

// -- Subtitle style ---------------------------------------------------------

const PRESET_COLORS: { label: string; value: string }[] = [
  { label: 'Kuning', value: '&H00D7FF&' },
  { label: 'Hijau', value: '&H00FF00&' },
  { label: 'Putih', value: '&H00FFFFFF&' },
  { label: 'Cyan', value: '&HFFFF00&' },
];

const CSS_COLOR_HINT: Record<string, string> = {
  '&H00D7FF&': '#FFD700',
  '&H00FF00&': '#00FF00',
  '&H00FFFFFF&': '#FFFFFF',
  '&HFFFF00&': '#00FFFF',
};

const SubtitleTab: React.FC = () => {
  const [style, setStyle] = useState<SubtitleStyle | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        setStyle(await getSubtitleStyle());
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Gagal memuat gaya subtitle.');
      }
    })();
  }, []);

  const update = (patch: Partial<SubtitleStyle>) => {
    setStyle((prev) => (prev ? { ...prev, ...patch } : prev));
    setMessage(null);
  };

  const handleSave = async () => {
    if (!style) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const saved = await saveSubtitleStyle(style);
      setStyle(saved);
      setMessage('Gaya subtitle tersimpan. Berlaku untuk proses berikutnya.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Gagal menyimpan.');
    } finally {
      setSaving(false);
    }
  };

  if (!style) {
    return <p className="card-description">{error ?? 'Memuat gaya subtitle...'}</p>;
  }

  const previewColor = CSS_COLOR_HINT[style.highlight_color] ?? '#FFD700';
  const words = ['Ini', 'contoh', 'subtitle', 'klip'];

  return (
    <div className="settings-section">
      {error && <div className="banner banner-error"><span>{error}</span></div>}
      {message && <div className="banner banner-info"><span>{message}</span></div>}

      <div className="subtitle-preview" data-position={style.position}>
        <span className="subtitle-preview-line">
          {words.slice(0, style.max_words).map((w, i) => (
            <span key={w} style={i === 0 ? { color: previewColor } : undefined}>
              {w}{' '}
            </span>
          ))}
          <span style={{ fontSize: `${Math.max(0.7, style.font_size / 72)}em` }} />
        </span>
      </div>

      <label className="form-label" htmlFor="sub-max-words">
        Kata per blok: {style.max_words}
      </label>
      <input
        id="sub-max-words"
        type="range"
        min={1}
        max={8}
        step={1}
        value={style.max_words}
        onChange={(e) => update({ max_words: Number(e.target.value) })}
      />

      <label className="form-label" htmlFor="sub-font-size">
        Ukuran font: {style.font_size}
      </label>
      <input
        id="sub-font-size"
        type="range"
        min={40}
        max={120}
        step={2}
        value={style.font_size}
        onChange={(e) => update({ font_size: Number(e.target.value) })}
      />

      <label className="form-label">Warna sorot kata aktif</label>
      <div className="subtitle-colors">
        {PRESET_COLORS.map((c) => (
          <button
            key={c.value}
            type="button"
            className={`subtitle-color ${style.highlight_color === c.value ? 'active' : ''}`}
            style={{ backgroundColor: CSS_COLOR_HINT[c.value] ?? '#FFD700' }}
            onClick={() => update({ highlight_color: c.value })}
            aria-label={c.label}
            title={c.label}
          />
        ))}
      </div>

      <label className="form-label">Posisi vertikal</label>
      <div className="segmented-control">
        {(['top', 'center', 'bottom'] as const).map((p) => (
          <button
            key={p}
            type="button"
            className={`segment-btn ${style.position === p ? 'active' : ''}`}
            onClick={() => update({ position: p })}
          >
            {p === 'top' ? 'Atas' : p === 'center' ? 'Tengah' : 'Bawah'}
          </button>
        ))}
      </div>

      <button type="button" className="btn-primary btn-inline" onClick={handleSave} disabled={saving}>
        {saving ? 'Menyimpan...' : 'Simpan Gaya Subtitle'}
      </button>

      <p className="form-helper">
        Subtitle dibuat dari transkripsi ASR lokal (gratis, offline) dengan blok frasa pendek —
        tidak lagi menumpuk antar-baris.
      </p>
    </div>
  );
};

// -- API Keys --------------------------------------------------------------

const KeysTab: React.FC = () => {
  const [keys, setKeys] = useState<LlmKeyStatus[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<{ provider: string; ok: boolean; message: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setKeys(await listLlmKeys());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Gagal memuat daftar API key.');
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleTest = async (provider: string) => {
    setBusy(provider);
    setError(null);
    setResult(null);
    try {
      const res = await testLlmKey(provider);
      setResult({ provider, ok: res.ok, message: res.message });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Tes koneksi gagal.');
    } finally {
      setBusy(null);
    }
  };

  const handleDelete = async (provider: string) => {
    if (!window.confirm(`Hapus API key tersimpan untuk ${provider}?`)) return;
    setBusy(provider);
    setResult(null);
    try {
      await deleteLlmKey(provider);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Gagal menghapus API key.');
    } finally {
      setBusy(null);
    }
  };

  const connected = keys.filter((k) => k.hasKey);
  const available = keys.filter((k) => !k.hasKey);

  return (
    <div className="settings-section">
      {error && <div className="banner banner-error"><span>{error}</span></div>}
      {result && (
        <div className={`banner ${result.ok ? 'banner-info' : 'banner-error'}`}>
          <span><strong>{result.provider}:</strong> {result.message}</span>
        </div>
      )}

      <h3 className="settings-subtitle">Tersambung ({connected.length})</h3>
      {connected.length === 0 ? (
        <p className="card-description">
          Belum ada API key tersimpan. Tambahkan dari form pembuatan klip (mode Otomatis).
        </p>
      ) : (
        <ul className="storage-list">
          {connected.map((k) => (
            <li key={k.provider} className="storage-row">
              <div className="storage-row-info">
                <strong>{k.provider}</strong>
                <span className="history-meta">
                  <span className="input-mono">{k.maskedKey ?? '***'}</span>
                  {k.model ? ` · ${k.model}` : ''}
                  {k.lastVerifiedOk === true ? ' · terverifikasi' : k.lastVerifiedOk === false ? ' · gagal terakhir' : ''}
                </span>
              </div>
              <div className="llm-key-actions">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => handleTest(k.provider)}
                  disabled={busy !== null}
                >
                  {busy === k.provider ? '...' : 'Tes'}
                </button>
                <button
                  type="button"
                  className="btn-secondary btn-danger"
                  onClick={() => handleDelete(k.provider)}
                  disabled={busy !== null}
                >
                  Hapus
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {available.length > 0 && (
        <>
          <h3 className="settings-subtitle">Belum tersambung ({available.length})</h3>
          <p className="history-meta" style={{ marginBottom: 0 }}>
            {available.map((k) => k.provider).join(', ')}
          </p>
        </>
      )}
    </div>
  );
};
