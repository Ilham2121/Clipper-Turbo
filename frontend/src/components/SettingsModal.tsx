import React, { useCallback, useEffect, useState } from 'react';
import {
  Activity,
  AlertCircle,
  Check,
  Clock,
  FolderOpen,
  HardDrive,
  KeyRound,
  Mic,
  RefreshCw,
  Smartphone,
  Sparkles,
  Subtitles,
  Trash2,
  X,
} from 'lucide-react';
import type {
  AsrSettings,
  LlmKeyStatus,
  StorageReport,
  SubtitleStyle,
  SystemDiagnostics,
} from '../api/client';
import {
  deleteLlmKey,
  formatBytes,
  getAsrSettings,
  getStorageReport,
  getSubtitleStyle,
  getSystemDiagnostics,
  listLlmKeys,
  purgeStorage,
  revealStorage,
  saveAsrSettings,
  saveSubtitleStyle,
  testLlmKey,
} from '../api/client';
import type { HistoryEntry } from '../history';

export type SettingsTab = 'history' | 'subtitle' | 'storage' | 'keys' | 'speech' | 'diagnostics';

export interface SettingsModalProps {
  open: boolean;
  entries: HistoryEntry[];
  onSelectHistory: (entry: HistoryEntry) => void;
  onRemoveHistory: (jobId: string) => void;
  onClearHistory: () => void;
  onClose: () => void;
  initialTab?: SettingsTab;
}

interface TabItem {
  key: SettingsTab;
  label: string;
  icon: React.ComponentType<{ className?: string; size?: number }>;
  badge?: number | string;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  open,
  entries,
  onSelectHistory,
  onRemoveHistory,
  onClearHistory,
  onClose,
  initialTab = 'subtitle',
}) => {
  const [tab, setTab] = useState<SettingsTab>(initialTab);

  // Close on Escape key
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // Prevent background scroll when modal is open
  useEffect(() => {
    if (open) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [open]);

  if (!open) return null;

  const tabs: TabItem[] = [
    { key: 'history', label: 'Riwayat Job', icon: Clock, badge: entries.length || undefined },
    { key: 'subtitle', label: 'Subtitle & Karaoke', icon: Subtitles },
    { key: 'storage', label: 'Storage & Cache', icon: HardDrive },
    { key: 'keys', label: 'API Key LLM', icon: KeyRound },
    { key: 'speech', label: 'Speech-to-Text', icon: Mic },
    { key: 'diagnostics', label: 'Diagnostik Sistem', icon: Activity },
  ];

  return (
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label="Pengaturan Sistem">
      <div className="modal-window" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="modal-header">
          <div className="modal-title-group">
            <h2 className="modal-title">Pengaturan Sistem</h2>
            <p className="modal-subtitle">Kelola preferensi subtitle, kapasitas penyimpanan, ASR, dan diagnostik</p>
          </div>
          <button
            type="button"
            className="modal-close-btn"
            onClick={onClose}
            aria-label="Tutup jendela pengaturan (Esc)"
            title="Tutup (Esc)"
          >
            <X size={18} />
          </button>
        </div>

        {/* 2-Column Body */}
        <div className="modal-body">
          {/* Navigation rail */}
          <nav className="modal-nav" role="tablist" aria-label="Kategori Pengaturan">
            {tabs.map((t) => {
              const Icon = t.icon;
              const isActive = tab === t.key;
              return (
                <button
                  key={t.key}
                  type="button"
                  role="tab"
                  aria-selected={isActive}
                  className={`modal-nav-item ${isActive ? 'active' : ''}`}
                  onClick={() => setTab(t.key)}
                >
                  <Icon size={16} className="modal-nav-icon" />
                  <span className="modal-nav-label">{t.label}</span>
                  {t.badge !== undefined && <span className="modal-nav-badge">{t.badge}</span>}
                </button>
              );
            })}
          </nav>

          {/* Active Content Pane */}
          <main className="modal-content-pane">
            {tab === 'history' && (
              <HistoryTab
                entries={entries}
                onSelect={(entry) => {
                  onSelectHistory(entry);
                  onClose();
                }}
                onRemove={onRemoveHistory}
                onClear={onClearHistory}
              />
            )}
            {tab === 'subtitle' && <SubtitleTab />}
            {tab === 'storage' && <StorageTab />}
            {tab === 'keys' && <KeysTab />}
            {tab === 'speech' && <SpeechTab />}
            {tab === 'diagnostics' && <DiagnosticsTab />}
          </main>
        </div>
      </div>
    </div>
  );
};

// ============================================================================
// TAB: Riwayat
// ============================================================================
const HistoryTab: React.FC<{
  entries: HistoryEntry[];
  onSelect: (entry: HistoryEntry) => void;
  onRemove: (jobId: string) => void;
  onClear: () => void;
}> = ({ entries, onSelect, onRemove, onClear }) => {
  if (entries.length === 0) {
    return (
      <div className="tab-empty-state">
        <Clock size={36} className="empty-icon" />
        <h3>Belum Ada Riwayat</h3>
        <p>Klip yang selesai dibuat akan tersimpan otomatis di sini.</p>
      </div>
    );
  }

  return (
    <div className="settings-section">
      <div className="section-header-row">
        <div>
          <h3 className="section-title">Riwayat Klip ({entries.length})</h3>
          <p className="section-description">Pilih entri untuk membuka hasil klip kembali di editor.</p>
        </div>
        <button
          type="button"
          className="btn-secondary btn-danger btn-sm"
          onClick={() => {
            if (window.confirm('Hapus seluruh riwayat klip? Data klip di penyimpanan tetap tersimpan.')) {
              onClear();
            }
          }}
        >
          <Trash2 size={14} /> Hapus Semua
        </button>
      </div>

      <div className="history-cards-grid">
        {entries.map((entry) => (
          <div key={entry.jobId} className="history-card">
            <div className="history-card-body" onClick={() => onSelect(entry)} role="button" tabIndex={0}>
              <span className="history-card-url" title={entry.youtubeUrl}>
                {entry.youtubeUrl}
              </span>
              <div className="history-card-meta">
                <span>{new Date(entry.createdAt).toLocaleDateString()}</span>
                <span>•</span>
                <span>{entry.clips.length} klip</span>
                <span>•</span>
                <span className={`status-pill pill-${entry.status}`}>{entry.status}</span>
              </div>
            </div>
            <button
              type="button"
              className="history-card-delete"
              onClick={() => onRemove(entry.jobId)}
              title="Hapus dari riwayat"
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
};

// ============================================================================
// TAB: Subtitle & Karaoke (with Live 9:16 Smartphone Preview)
// ============================================================================
const PRESET_COLORS: { label: string; value: string; cssColor: string }[] = [
  { label: 'Kuning Amber', value: '&H00D7FF&', cssColor: '#FFD700' },
  { label: 'Hijau Neon', value: '&H00FF00&', cssColor: '#00FF66' },
  { label: 'Cyan Electric', value: '&HFFFF00&', cssColor: '#00E5FF' },
  { label: 'Putih Bersih', value: '&H00FFFFFF&', cssColor: '#FFFFFF' },
  { label: 'Magenta Pink', value: '&H00C8FF&', cssColor: '#FF69B4' },
];

const SubtitleTab: React.FC = () => {
  const [style, setStyle] = useState<SubtitleStyle | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeWordIndex, setActiveWordIndex] = useState(0);

  useEffect(() => {
    void (async () => {
      try {
        setStyle(await getSubtitleStyle());
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Gagal memuat gaya subtitle.');
      }
    })();
  }, []);

  // Cycle active word in preview every 700ms to simulate karaoke highlight
  useEffect(() => {
    const timer = setInterval(() => {
      setActiveWordIndex((prev) => (prev + 1) % 4);
    }, 750);
    return () => clearInterval(timer);
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
      setMessage('Gaya subtitle tersimpan! Berlaku untuk semua klip berikutnya.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Gagal menyimpan.');
    } finally {
      setSaving(false);
    }
  };

  if (!style) {
    return <div className="tab-loading">Memuat konfigurasi subtitle...</div>;
  }

  const activeColorObj = PRESET_COLORS.find((c) => c.value === style.highlight_color) || PRESET_COLORS[0];
  const previewWords = ['Ini', 'contoh', 'subtitle', 'viral'];

  return (
    <div className="subtitle-tab-layout">
      {/* Controls column */}
      <div className="subtitle-controls-col">
        <h3 className="section-title">Gaya & Sinkronisasi Subtitle</h3>
        <p className="section-description">
          Sesuaikan tampilan teks karaoke untuk video vertikal 9:16 (TikTok, Reels, Shorts).
        </p>

        {error && <div className="banner banner-error"><AlertCircle size={15} /><span>{error}</span></div>}
        {message && <div className="banner banner-info"><Check size={15} /><span>{message}</span></div>}

        {/* Mode Sinkronisasi */}
        <div className="control-group">
          <label className="form-label">Mode Sinkronisasi Karaoke</label>
          <div className="segmented-control">
            <button
              type="button"
              className={`segment-btn ${style.sync_mode === 'word' ? 'active' : ''}`}
              onClick={() => update({ sync_mode: 'word' })}
            >
              <Sparkles size={14} /> Per-Kata (Karaoke Dinamis)
            </button>
            <button
              type="button"
              className={`segment-btn ${style.sync_mode !== 'word' ? 'active' : ''}`}
              onClick={() => update({ sync_mode: 'block' })}
            >
              Per-Frasa (Blok Standar)
            </button>
          </div>
          <span className="input-helper">
            {style.sync_mode === 'word'
              ? 'Warna sorotan berpindah per kata mengikuti audio vokal secara presisi (efek karaoke viral).'
              : 'Menyorot kata utama dalam satu blok durasi kalimat.'}
          </span>
        </div>

        {/* Kata per blok */}
        <div className="control-group">
          <div className="slider-label-row">
            <label className="form-label" htmlFor="max-words">Kata per Baris/Frasa</label>
            <span className="slider-val-badge">{style.max_words} kata</span>
          </div>
          <input
            id="max-words"
            type="range"
            min={1}
            max={6}
            step={1}
            value={style.max_words}
            onChange={(e) => update({ max_words: Number(e.target.value) })}
          />
        </div>

        {/* Ukuran Font */}
        <div className="control-group">
          <div className="slider-label-row">
            <label className="form-label" htmlFor="font-size">Ukuran Font</label>
            <span className="slider-val-badge">{style.font_size} px</span>
          </div>
          <input
            id="font-size"
            type="range"
            min={40}
            max={110}
            step={2}
            value={style.font_size}
            onChange={(e) => update({ font_size: Number(e.target.value) })}
          />
        </div>

        {/* Warna Highlight */}
        <div className="control-group">
          <label className="form-label">Warna Sorot Kata Aktif</label>
          <div className="color-swatches-row">
            {PRESET_COLORS.map((c) => (
              <button
                key={c.value}
                type="button"
                className={`color-swatch-btn ${style.highlight_color === c.value ? 'selected' : ''}`}
                style={{ backgroundColor: c.cssColor }}
                onClick={() => update({ highlight_color: c.value })}
                title={c.label}
              >
                {style.highlight_color === c.value && <Check size={14} color="#000" strokeWidth={3} />}
              </button>
            ))}
          </div>
        </div>

        {/* Posisi Vertikal */}
        <div className="control-group">
          <label className="form-label">Posisi Vertikal</label>
          <div className="segmented-control">
            {(['top', 'center', 'bottom'] as const).map((pos) => (
              <button
                key={pos}
                type="button"
                className={`segment-btn ${style.position === pos ? 'active' : ''}`}
                onClick={() => update({ position: pos })}
              >
                {pos === 'top' ? 'Atas' : pos === 'center' ? 'Tengah' : 'Bawah'}
              </button>
            ))}
          </div>
        </div>

        <button
          type="button"
          className="btn-primary"
          style={{ width: '100%', marginTop: '0.5rem' }}
          onClick={handleSave}
          disabled={saving}
        >
          {saving ? 'Menyimpan Perubahan...' : 'Simpan Pengaturan Subtitle'}
        </button>
      </div>

      {/* Live 9:16 Smartphone Mockup Preview */}
      <div className="subtitle-preview-col">
        <div className="preview-heading">
          <Smartphone size={16} /> Live Preview 9:16
        </div>
        <div className="phone-mockup">
          <div className="phone-notch" />
          <div className="phone-screen" data-position={style.position}>
            {/* Ambient simulated video visual */}
            <div className="simulated-video-bg">
              <div className="simulated-speaker-avatar" />
              <div className="simulated-ui-overlay">
                <div className="simulated-action-bar">
                  <span className="simulated-icon" />
                  <span className="simulated-icon" />
                  <span className="simulated-icon" />
                </div>
              </div>
            </div>

            {/* Rendered subtitle preview */}
            <div className={`preview-subtitle-box pos-${style.position}`}>
              <div
                className="preview-subtitle-text"
                style={{
                  fontSize: `${Math.round(style.font_size * 0.22)}px`,
                }}
              >
                {previewWords.slice(0, style.max_words).map((word, idx) => {
                  const isHighlighted =
                    style.sync_mode === 'word'
                      ? idx === activeWordIndex % style.max_words
                      : idx === 0;
                  return (
                    <span
                      key={word}
                      className="preview-word"
                      style={{
                        color: isHighlighted ? activeColorObj.cssColor : '#FFFFFF',
                        textShadow: isHighlighted
                          ? `0 0 10px ${activeColorObj.cssColor}88, 0 2px 4px rgba(0,0,0,0.9)`
                          : '0 2px 4px rgba(0,0,0,0.9)',
                      }}
                    >
                      {word}{' '}
                    </span>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
        <span className="preview-caption">Pratinjau proporsi dan posisi dalam layar 9:16</span>
      </div>
    </div>
  );
};

// ============================================================================
// TAB: Storage & Cache
// ============================================================================
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
    if (!window.confirm(`Hapus "${label}"? Klip yang sudah selesai diproses tidak akan dihapus.`)) return;
    setBusy(category);
    setMessage(null);
    setError(null);
    try {
      const res = await purgeStorage(category);
      setMessage(`Berhasil membersihkan: ${formatBytes(res.freedBytes)} (${res.removedFiles} file dihapus).`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Gagal membersihkan storage.');
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
      setError(err instanceof Error ? err.message : 'Gagal membuka folder di sistem.');
    }
  };

  if (!report && !error) {
    return <div className="tab-loading">Memuat data kapasitas penyimpanan...</div>;
  }

  const purgeable = report?.purgeableBytes || 0;
  const retained = report?.retainedBytes || 0;
  const total = purgeable + retained || 1;
  const purgeablePct = Math.round((purgeable / total) * 100);
  const retainedPct = 100 - purgeablePct;

  return (
    <div className="settings-section">
      <div className="section-header-row">
        <div>
          <h3 className="section-title">Kapasitas Penyimpanan & Cache</h3>
          <p className="section-description">Kelola file video mentah, cache unduhan, dan folder hasil klip.</p>
        </div>
        <button type="button" className="btn-secondary btn-sm" onClick={() => void refresh()} disabled={busy !== null}>
          <RefreshCw size={14} /> Segarkan
        </button>
      </div>

      {error && <div className="banner banner-error"><AlertCircle size={15} /><span>{error}</span></div>}
      {message && <div className="banner banner-info"><Check size={15} /><span>{message}</span></div>}

      {report && (
        <>
          {/* Storage usage bar */}
          <div className="storage-meter-card">
            <div className="storage-meter-labels">
              <span>Penggunaan Ruang Disk</span>
              <strong>{formatBytes(total)} Total</strong>
            </div>
            <div className="storage-progress-track">
              <div className="storage-bar-retained" style={{ width: `${retainedPct}%` }} title={`Klip Aktif: ${formatBytes(retained)}`} />
              <div className="storage-bar-purgeable" style={{ width: `${purgeablePct}%` }} title={`Sampah/Cache: ${formatBytes(purgeable)}`} />
            </div>
            <div className="storage-meter-legend">
              <span className="legend-item"><span className="legend-dot dot-retained" /> Klip Aktif ({formatBytes(retained)})</span>
              <span className="legend-item"><span className="legend-dot dot-purgeable" /> Sampah & Cache ({formatBytes(purgeable)})</span>
            </div>
          </div>

          {/* Quick Metrics */}
          <div className="storage-metrics-cards">
            <div className="metric-box">
              <span className="metric-val">{formatBytes(report.purgeableBytes)}</span>
              <span className="metric-lbl">Dapat Dibersihkan</span>
            </div>
            <div className="metric-box">
              <span className="metric-val">{formatBytes(report.retainedBytes)}</span>
              <span className="metric-lbl">Klip Aktif</span>
            </div>
            <div className="metric-box">
              <span className="metric-val">{report.jobCount}</span>
              <span className="metric-lbl">Total Job ({report.fileCount} file)</span>
            </div>
          </div>

          <button
            type="button"
            className="btn-primary"
            style={{ width: '100%', marginBottom: '1.25rem' }}
            onClick={() => handlePurge('all')}
            disabled={busy !== null || report.purgeableBytes === 0}
          >
            <Trash2 size={16} /> {busy === 'all' ? 'Membersihkan...' : 'Bersihkan Semua Sampah & Cache'}
          </button>

          {/* Categories breakdown */}
          <h4 className="subsection-title">Rincian Kategori</h4>
          <div className="storage-category-list">
            {report.categories.map((cat) => (
              <div key={cat.category} className="storage-category-row">
                <div className="storage-cat-info">
                  <strong>{cat.label}</strong>
                  <span className="storage-cat-meta">{formatBytes(cat.bytes)} • {cat.files} file</span>
                </div>
                <button
                  type="button"
                  className="btn-secondary btn-danger btn-sm"
                  onClick={() => handlePurge(cat.category)}
                  disabled={busy !== null || cat.files === 0}
                >
                  {busy === cat.category ? '...' : 'Bersihkan'}
                </button>
              </div>
            ))}
          </div>

          {/* File explorer actions */}
          <div className="storage-system-actions">
            <button type="button" className="btn-secondary btn-sm" onClick={() => handleReveal('jobs')}>
              <FolderOpen size={14} /> Buka Folder Jobs
            </button>
            <button type="button" className="btn-secondary btn-sm" onClick={() => handleReveal('root')}>
              <FolderOpen size={14} /> Buka Folder Storage
            </button>
          </div>
        </>
      )}
    </div>
  );
};

// ============================================================================
// TAB: API Key LLM
// ============================================================================
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

  return (
    <div className="settings-section">
      <h3 className="section-title">Koneksi API Key LLM</h3>
      <p className="section-description">
        Kunci API digunakan oleh seleksi segmen AI otomatis (OpenRouter, Groq, NVIDIA NIM, DeepSeek, OpenAI).
      </p>

      {error && <div className="banner banner-error"><AlertCircle size={15} /><span>{error}</span></div>}
      {result && (
        <div className={`banner ${result.ok ? 'banner-info' : 'banner-error'}`}>
          {result.ok ? <Check size={15} /> : <AlertCircle size={15} />}
          <span><strong>{result.provider}:</strong> {result.message}</span>
        </div>
      )}

      <h4 className="subsection-title">Tersambung ({connected.length})</h4>
      {connected.length === 0 ? (
        <p className="card-description">
          Belum ada API key tersimpan. Anda dapat memasukkan key saat membuat job di mode Seleksi Otomatis.
        </p>
      ) : (
        <div className="key-items-list">
          {connected.map((k) => (
            <div key={k.provider} className="key-card">
              <div className="key-card-info">
                <span className="key-provider-name">{k.provider}</span>
                <span className="key-details">
                  <code>{k.maskedKey ?? '***'}</code>
                  {k.model ? ` • ${k.model}` : ''}
                  {k.lastVerifiedOk === true && <span className="verified-badge">Terverifikasi</span>}
                </span>
              </div>
              <div className="key-card-actions">
                <button
                  type="button"
                  className="btn-secondary btn-sm"
                  onClick={() => handleTest(k.provider)}
                  disabled={busy !== null}
                >
                  {busy === k.provider ? '...' : 'Tes Koneksi'}
                </button>
                <button
                  type="button"
                  className="btn-secondary btn-danger btn-sm"
                  onClick={() => handleDelete(k.provider)}
                  disabled={busy !== null}
                >
                  Hapus
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// ============================================================================
// TAB: Speech-to-Text (ASR)
// ============================================================================
const SpeechTab: React.FC = () => {
  const [asr, setAsr] = useState<AsrSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [glossaryInput, setGlossaryInput] = useState('');

  useEffect(() => {
    void (async () => {
      try {
        const data = await getAsrSettings();
        setAsr(data);
        setGlossaryInput((data.glossary || []).join(', '));
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Gagal memuat konfigurasi ASR.');
      }
    })();
  }, []);

  const handleSave = async () => {
    if (!asr) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const glossaryList = glossaryInput
        .split(',')
        .map((w) => w.trim())
        .filter(Boolean);
      const saved = await saveAsrSettings({
        ...asr,
        glossary: glossaryList,
      });
      setAsr(saved);
      setMessage('Konfigurasi Speech-to-Text tersimpan.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Gagal menyimpan.');
    } finally {
      setSaving(false);
    }
  };

  if (!asr) {
    return <div className="tab-loading">Memuat konfigurasi Speech-to-Text...</div>;
  }

  return (
    <div className="settings-section">
      <h3 className="section-title">Konfigurasi Pengenalan Suara (ASR)</h3>
      <p className="section-description">
        Optimalkan akurasi pengenalan kata dan sinkronisasi suara untuk bahasa Indonesia.
      </p>

      {error && <div className="banner banner-error"><AlertCircle size={15} /><span>{error}</span></div>}
      {message && <div className="banner banner-info"><Check size={15} /><span>{message}</span></div>}

      {/* Provider selection */}
      <div className="control-group">
        <label className="form-label">Mesin Transkripsi (Engine)</label>
        <select
          className="form-input"
          value={asr.provider}
          onChange={(e) => setAsr({ ...asr, provider: e.target.value })}
        >
          <option value="local">Lokal (faster-whisper) — Gratis & Offline</option>
          <option value="groq">Groq Cloud (whisper-large-v3-turbo) — Kecepatan Ultra</option>
          <option value="openai">OpenAI (whisper-1)</option>
        </select>
      </div>

      {/* Model Whisper */}
      <div className="control-group">
        <label className="form-label">Ukuran Model Whisper</label>
        <select
          className="form-input"
          value={asr.model}
          onChange={(e) => setAsr({ ...asr, model: e.target.value })}
        >
          <option value="small">small (Default CPU - Cepat & Stabil)</option>
          <option value="medium">medium (Keseimbangan)</option>
          <option value="large-v3-turbo">large-v3-turbo (Akurasi Maksimal)</option>
        </select>
        <span className="input-helper">Model `small` sangat optimal untuk CPU 4-8 core.</span>
      </div>

      {/* VAD Filter */}
      <div className="control-group switch-group">
        <div>
          <label className="form-label" style={{ marginBottom: 0 }}>VAD Filter (Voice Activity Detection)</label>
          <span className="input-helper">
            Secara otomatis mendeteksi suara manusia dan memotong jeda musik/keheningan agar tidak ada halusinasi kata.
          </span>
        </div>
        <input
          type="checkbox"
          checked={asr.vad_filter}
          onChange={(e) => setAsr({ ...asr, vad_filter: e.target.checked })}
          style={{ width: 18, height: 18, cursor: 'pointer' }}
        />
      </div>

      {/* Glossary / Vocab */}
      <div className="control-group">
        <label className="form-label">Kosakata Khusus (Custom Glossary & Hotwords)</label>
        <textarea
          className="form-input"
          rows={3}
          placeholder="Contoh: Prabowo, Gibran, Deddy Corbuzier, podcast, cringe, FYP"
          value={glossaryInput}
          onChange={(e) => setGlossaryInput(e.target.value)}
        />
        <span className="input-helper">
          Pisahkan dengan koma. Kata-kata ini akan diprioritaskan oleh model ASR agar ejaan nama tokoh atau istilah gaul tidak salah.
        </span>
      </div>

      <button
        type="button"
        className="btn-primary"
        style={{ width: '100%', marginTop: '0.5rem' }}
        onClick={handleSave}
        disabled={saving}
      >
        {saving ? 'Menyimpan...' : 'Simpan Konfigurasi ASR'}
      </button>
    </div>
  );
};

// ============================================================================
// TAB: Diagnostik Sistem
// ============================================================================
const DiagnosticsTab: React.FC = () => {
  const [diag, setDiag] = useState<SystemDiagnostics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setDiag(await getSystemDiagnostics());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Gagal memuat diagnostik.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (loading) {
    return <div className="tab-loading">Memeriksa status perangkat dan lingkungan...</div>;
  }

  return (
    <div className="settings-section">
      <div className="section-header-row">
        <div>
          <h3 className="section-title">Diagnostik Lingkungan Server</h3>
          <p className="section-description">Informasi perangkat keras, akselerasi media, dan status runtime.</p>
        </div>
        <button type="button" className="btn-secondary btn-sm" onClick={() => void refresh()}>
          <RefreshCw size={14} /> Segarkan
        </button>
      </div>

      {error && <div className="banner banner-error"><AlertCircle size={15} /><span>{error}</span></div>}

      {diag && (
        <div className="diag-grid">
          <div className="diag-card">
            <span className="diag-title">Sistem Operasi</span>
            <span className="diag-value">{diag.os}</span>
          </div>

          <div className="diag-card">
            <span className="diag-title">Runtime Python</span>
            <span className="diag-value">Python {diag.python}</span>
          </div>

          <div className="diag-card">
            <span className="diag-title">Akselerasi FFmpeg</span>
            <span className={`diag-badge ${diag.ffmpegInstalled ? 'ok' : 'err'}`}>
              {diag.ffmpegInstalled ? 'Terpasang & Siap' : 'Tidak Ditemukan'}
            </span>
            <span className="diag-subtext">{diag.ffmpegPath}</span>
          </div>

          <div className="diag-card">
            <span className="diag-title">Akselerasi CUDA / GPU</span>
            <span className={`diag-badge ${diag.cudaAvailable ? 'ok' : 'info'}`}>
              {diag.cudaAvailable ? `Aktif (${diag.cudaDeviceCount} GPU)` : 'Mode CPU (C-Translate2)'}
            </span>
          </div>

          <div className="diag-card">
            <span className="diag-title">Database SQLite</span>
            <span className="diag-value input-mono">{diag.dbPath}</span>
          </div>

          <div className="diag-card">
            <span className="diag-title">Kapasitas Antrian Job</span>
            <span className="diag-value">{diag.activeJobs} aktif / {diag.maxConcurrent} batas simultan</span>
          </div>
        </div>
      )}
    </div>
  );
};
