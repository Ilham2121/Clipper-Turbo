import React, { useState, useEffect } from 'react';
import {
  Smartphone,
  Monitor,
  Sparkles,
  Clock,
  Settings2,
  ChevronDown,
  ChevronUp,
  Loader2,
  Zap,
  Film,
  AlertCircle,
} from 'lucide-react';
import type { AspectRatio, CreateJobFormData, ManualSegment, EstimateResponse } from '../api/client';
import { LLM_PRESETS, getEstimate, formatEstimateRange } from '../api/client';
import { LlmKeyManager } from './LlmKeyManager';
import { ManualTimeline } from './ManualTimeline';
import type { ManualInputRow } from './manualSegments';
import { parseTimeToSeconds } from './manualSegments';

interface JobFormProps {
  onSubmit: (payload: CreateJobFormData) => Promise<void>;
  isSubmitting: boolean;
  /** True while another job is still running (submit blocked, form stays visible). */
  disabled?: boolean;
  onOpenSettings?: () => void;
}

function extractYouTubeId(url: string): string | null {
  if (!url) return null;
  const match = url.match(
    /(?:youtu\.be\/|youtube\.com\/(?:embed\/|v\/|shorts\/|watch\?v=|watch\?.+&v=))([\w-]{11})/,
  );
  return match ? match[1] : null;
}

function formatDurationSeconds(seconds?: number): string {
  if (!seconds || seconds <= 0) return '00:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const mm = m.toString().padStart(2, '0');
  const ss = s.toString().padStart(2, '0');
  return `${mm}:${ss}`;
}

export const JobForm: React.FC<JobFormProps> = ({ onSubmit, isSubmitting, disabled, onOpenSettings }) => {
  const [url, setUrl] = useState('');
  const [mode, setMode] = useState<'auto' | 'manual'>('auto');

  // Video preview & estimation
  const [detectedVideoId, setDetectedVideoId] = useState<string | null>(null);
  const [estimate, setEstimate] = useState<EstimateResponse | null>(null);
  const [isEstimating, setIsEstimating] = useState<boolean>(false);

  // LLM Config
  const [provider, setProvider] = useState<string>('openrouter');
  const [model, setModel] = useState(LLM_PRESETS.openrouter.defaultModel);
  const [customEndpoint, setCustomEndpoint] = useState('');
  const [hasStoredKey, setHasStoredKey] = useState<boolean>(false);

  // Manual Segments
  const [manualRows, setManualRows] = useState<ManualInputRow[]>([
    { id: '1', start: '00:30', end: '01:15', label: 'Momen Pilihan' },
  ]);

  // Aspect Ratios & Subtitles
  const [ratio916, setRatio916] = useState(true);
  const [ratio169, setRatio169] = useState(false);
  const [burnSubtitles, setBurnSubtitles] = useState(true);

  // Advanced Options
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [reframeMode, setReframeMode] = useState<'auto' | 'speaker_focus' | 'split_stack' | 'center_crop'>('auto');

  const [formError, setFormError] = useState<string | null>(null);

  // Live YouTube video detection & estimate probe
  useEffect(() => {
    const vid = extractYouTubeId(url.trim());
    setDetectedVideoId(vid);

    if (!vid) {
      setEstimate(null);
      setIsEstimating(false);
      return;
    }

    let isCurrent = true;
    setIsEstimating(true);

    const timer = window.setTimeout(() => {
      getEstimate(url.trim())
        .then((res) => {
          if (isCurrent) {
            setEstimate(res);
            setIsEstimating(false);
          }
        })
        .catch(() => {
          if (isCurrent) {
            setIsEstimating(false);
          }
        });
    }, 450);

    return () => {
      isCurrent = false;
      window.clearTimeout(timer);
    };
  }, [url]);

  const handleProviderChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const nextProvider = e.target.value;
    setProvider(nextProvider);
    if (LLM_PRESETS[nextProvider]) {
      setModel(LLM_PRESETS[nextProvider].defaultModel);
      if (nextProvider === 'custom') {
        setCustomEndpoint('http://localhost:11434/v1');
      } else {
        setCustomEndpoint('');
      }
    }
  };

  const handleAddSegmentRow = () => {
    setManualRows((prev) => [
      ...prev,
      { id: Date.now().toString(), start: '', end: '', label: '' },
    ]);
  };

  const handleRemoveSegmentRow = (id: string) => {
    if (manualRows.length <= 1) return;
    setManualRows((prev) => prev.filter((r) => r.id !== id));
  };

  const handleUpdateSegmentRow = (id: string, field: keyof ManualInputRow, val: string) => {
    setManualRows((prev) =>
      prev.map((r) => (r.id === id ? { ...r, [field]: val } : r))
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    if (disabled) {
      setFormError('Masih ada job berjalan. Tunggu selesai atau bersihkan job aktif terlebih dahulu.');
      return;
    }

    const trimmedUrl = url.trim();
    if (!trimmedUrl) {
      setFormError('Masukkan URL video YouTube terlebih dahulu.');
      return;
    }
    if (!trimmedUrl.includes('youtube.com') && !trimmedUrl.includes('youtu.be')) {
      setFormError('URL harus berasal dari YouTube (contoh: https://www.youtube.com/watch?v=... atau https://youtu.be/...)');
      return;
    }

    const aspectRatios: AspectRatio[] = [];
    if (ratio916) aspectRatios.push('9:16');
    if (ratio169) aspectRatios.push('16:9');
    if (aspectRatios.length === 0) {
      setFormError('Pilih minimal satu rasio video (9:16 atau 16:9).');
      return;
    }

    const formData: CreateJobFormData = {
      youtubeUrl: trimmedUrl,
      selectionMode: mode,
      outputFormats: aspectRatios,
      burnSubtitles: burnSubtitles,
      reframeMode: reframeMode,
    };

    if (mode === 'auto') {
      if (!hasStoredKey) {
        setFormError('Simpan API Key untuk provider ini terlebih dahulu.');
        return;
      }
      formData.llmProvider = provider;
      if (model.trim()) formData.llmModel = model.trim();
      if (provider === 'custom' && customEndpoint.trim()) {
        formData.llmBaseUrl = customEndpoint.trim();
      }
    } else {
      const parsedSegments: ManualSegment[] = [];
      for (const row of manualRows) {
        const startSec = parseTimeToSeconds(row.start);
        const endSec = parseTimeToSeconds(row.end);
        if (endSec <= startSec) {
          setFormError(`Timestamp akhir (${row.end}) harus lebih besar dari timestamp awal (${row.start}).`);
          return;
        }
        parsedSegments.push({
          start: startSec,
          end: endSec,
        });
      }
      if (parsedSegments.length === 0) {
        setFormError('Tambahkan minimal satu segmen manual.');
        return;
      }
      formData.manualSegments = parsedSegments;
    }

    try {
      await onSubmit(formData);
    } catch (err: unknown) {
      if (err instanceof Error) {
        setFormError(err.message);
      } else {
        setFormError('Terjadi kesalahan saat memproses form.');
      }
    }
  };

  const currentPreset = LLM_PRESETS[provider];
  const busy = isSubmitting || !!disabled;

  return (
    <form className="card" onSubmit={handleSubmit}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
        <h2 className="card-title" style={{ margin: 0 }}>Studio Composer</h2>
        <span className="badge-pill badge-pill-eta">
          <Sparkles size={12} /> AI Powered
        </span>
      </div>
      <p className="card-description">
        Generate klip vertikal dan horizontal otomatis dengan transkripsi presisi dan reframing cerdas.
      </p>

      {formError && (
        <div className="banner banner-error">
          <AlertCircle size={16} />
          <span>{formError}</span>
        </div>
      )}

      {disabled && (
        <div className="banner banner-info">
          <Clock size={16} />
          <span>Job sedang diproses — pantau progres pada panel di sebelah kanan.</span>
        </div>
      )}

      {/* URL Input */}
      <div className="form-group">
        <label className="form-label" htmlFor="yt-url">
          URL Video YouTube
        </label>
        <div style={{ position: 'relative' }}>
          <input
            id="yt-url"
            type="url"
            className="input-text"
            placeholder="https://www.youtube.com/watch?v=... atau https://youtu.be/..."
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            required
            disabled={busy}
          />
        </div>

        {/* Live Video Preview Card */}
        {detectedVideoId && (
          <div className="video-preview-card">
            <div className="video-preview-thumb-wrap">
              <img
                src={`https://img.youtube.com/vi/${detectedVideoId}/mqdefault.jpg`}
                alt="Thumbnail"
                className="video-preview-thumb"
              />
              <span className="video-preview-duration">
                {formatDurationSeconds(estimate?.duration)}
              </span>
            </div>
            <div className="video-preview-info">
              <span className="video-preview-title">
                {isEstimating ? 'Mendeteksi video...' : estimate?.title || 'Video YouTube Terdeteksi'}
              </span>
              <div className="video-preview-badges">
                {estimate && (
                  <span className="badge-pill badge-pill-eta" title="Perkiraan waktu penyelesaian">
                    <Clock size={11} /> {formatEstimateRange(estimate.estimatedSeconds, estimate.etaLowSeconds, estimate.etaHighSeconds)}
                  </span>
                )}
                {estimate?.hardwareAccelerated !== undefined && (
                  <span className="badge-pill badge-pill-hw" title="Akselerasi hardware video encoder">
                    <Zap size={11} /> {estimate.hardwareAccelerated ? 'GPU Accelerated' : 'CPU Encoding'}
                  </span>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Mode Selection */}
      <div className="form-group">
        <label className="form-label">Mode Kurasi Klip</label>
        <div className="segmented-control">
          <button
            type="button"
            className={`segment-btn ${mode === 'auto' ? 'active' : ''}`}
            onClick={() => setMode('auto')}
            disabled={busy}
          >
            <Sparkles size={14} style={{ display: 'inline', verticalAlign: 'text-bottom', marginRight: '5px' }} />
            Otomatis (AI LLM)
          </button>
          <button
            type="button"
            className={`segment-btn ${mode === 'manual' ? 'active' : ''}`}
            onClick={() => setMode('manual')}
            disabled={busy}
          >
            <Clock size={14} style={{ display: 'inline', verticalAlign: 'text-bottom', marginRight: '5px' }} />
            Manual (Timestamp)
          </button>
        </div>
      </div>

      {/* Auto Mode Config */}
      {mode === 'auto' && (
        <div
          className="form-group"
          style={{
            padding: '1rem',
            background: 'var(--bg-input)',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-subtle)',
          }}
        >
          <div className="form-group">
            <label className="form-label" htmlFor="llm-provider">
              Pilihan Model AI
            </label>
            <select
              id="llm-provider"
              className="select-input"
              value={provider}
              onChange={handleProviderChange}
              disabled={busy}
            >
              {Object.entries(LLM_PRESETS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v.label}
                </option>
              ))}
            </select>
            {currentPreset && <p className="form-helper">{currentPreset.tip}</p>}
          </div>

          <LlmKeyManager
            provider={provider}
            model={model}
            customEndpoint={customEndpoint}
            onHasKeyChange={setHasStoredKey}
            disabled={busy}
          />
        </div>
      )}

      {/* Manual Mode Config */}
      {mode === 'manual' && (
        <div
          className="form-group"
          style={{
            padding: '1rem',
            background: 'var(--bg-input)',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-subtle)',
          }}
        >
          <label className="form-label">Tentukan Segmen Timestamp (MM:SS)</label>
          <ManualTimeline
            url={url}
            rows={manualRows}
            onUpdateRow={handleUpdateSegmentRow}
          />

          {manualRows.map((row) => (
            <div
              key={row.id}
              style={{
                display: 'flex',
                gap: '0.5rem',
                alignItems: 'center',
                marginTop: '0.5rem',
              }}
            >
              <input
                type="text"
                className="input-text input-mono"
                placeholder="Start (00:30)"
                value={row.start}
                onChange={(e) => handleUpdateSegmentRow(row.id, 'start', e.target.value)}
                disabled={busy}
                style={{ maxWidth: '130px' }}
                required
              />
              <span style={{ color: 'var(--text-muted)' }}>—</span>
              <input
                type="text"
                className="input-text input-mono"
                placeholder="End (01:15)"
                value={row.end}
                onChange={(e) => handleUpdateSegmentRow(row.id, 'end', e.target.value)}
                disabled={busy}
                style={{ maxWidth: '130px' }}
                required
              />
              <input
                type="text"
                className="input-text"
                placeholder="Label momen"
                value={row.label}
                onChange={(e) => handleUpdateSegmentRow(row.id, 'label', e.target.value)}
                disabled={busy}
              />
              {manualRows.length > 1 && (
                <button
                  type="button"
                  className="btn-secondary btn-danger"
                  onClick={() => handleRemoveSegmentRow(row.id)}
                  disabled={busy}
                >
                  ✕
                </button>
              )}
            </div>
          ))}

          <button
            type="button"
            className="btn-secondary"
            onClick={handleAddSegmentRow}
            disabled={busy}
            style={{ marginTop: '0.75rem', fontSize: '0.825rem' }}
          >
            + Tambah Baris Segmen
          </button>
        </div>
      )}

      {/* Aspect Ratio Cards */}
      <div className="form-group">
        <label className="form-label">Rasio Format Klip</label>
        <div className="aspect-selector-grid">
          <div
            className={`aspect-tile ${ratio916 ? 'selected' : ''}`}
            onClick={() => !busy && setRatio916(!ratio916)}
            role="checkbox"
            aria-checked={ratio916}
            tabIndex={0}
          >
            <div className="aspect-tile-header">
              <div className="aspect-tile-icon-wrap">
                <Smartphone size={18} color="var(--accent-primary)" />
                <span>9:16 Vertikal</span>
              </div>
              <input
                type="checkbox"
                checked={ratio916}
                onChange={() => {}}
                style={{ accentColor: 'var(--accent-primary)' }}
                disabled={busy}
              />
            </div>
            <p className="aspect-tile-desc">
              Untuk TikTok, YouTube Shorts, Reels dengan auto-tracking wajah.
            </p>
          </div>

          <div
            className={`aspect-tile ${ratio169 ? 'selected' : ''}`}
            onClick={() => !busy && setRatio169(!ratio169)}
            role="checkbox"
            aria-checked={ratio169}
            tabIndex={0}
          >
            <div className="aspect-tile-header">
              <div className="aspect-tile-icon-wrap">
                <Monitor size={18} color="var(--accent-secondary)" />
                <span>16:9 Horizontal</span>
              </div>
              <input
                type="checkbox"
                checked={ratio169}
                onChange={() => {}}
                style={{ accentColor: 'var(--accent-secondary)' }}
                disabled={busy}
              />
            </div>
            <p className="aspect-tile-desc">
              Format standar landscape untuk feed YouTube desktop & video reguler.
            </p>
          </div>
        </div>
      </div>

      {/* Burn-in Subtitles Switch */}
      <div className="form-group">
        <div
          className="switch-control-card"
          onClick={() => !busy && setBurnSubtitles(!burnSubtitles)}
          role="switch"
          aria-checked={burnSubtitles}
          tabIndex={0}
        >
          <div className="switch-label-wrap">
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Sparkles size={16} color="var(--accent-primary)" />
              <span className="switch-title">Burn-in Subtitle & Karaoke</span>
            </div>
            <span className="switch-desc">
              {burnSubtitles
                ? 'Subtitle di-render langsung ke video dengan highlight per-kata.'
                : 'Tanpa subtitle video (hanya export sidecar .srt gratis).'}
            </span>
          </div>

          <div className={`switch-track ${burnSubtitles ? 'active' : ''}`}>
            <div className="switch-thumb" />
          </div>
        </div>
      </div>

      {/* Advanced Options Accordion */}
      <div className="disclosure-card">
        <button
          type="button"
          className="disclosure-trigger"
          onClick={() => setAdvancedOpen(!advancedOpen)}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Settings2 size={16} />
            <span>Pengaturan Lanjutan (Reframing & Subtitle)</span>
          </div>
          {advancedOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>

        {advancedOpen && (
          <div className="disclosure-body">
            <div>
              <label className="form-label" htmlFor="reframe-mode">
                Strategi Reframing 9:16
              </label>
              <select
                id="reframe-mode"
                className="select-input"
                value={reframeMode}
                onChange={(e) => setReframeMode(e.target.value as any)}
                disabled={busy}
              >
                <option value="auto">Auto (Deteksi Tata Letak & Active Speaker)</option>
                <option value="speaker_focus">Speaker Focus (Fokus Wajah Pembicara)</option>
                <option value="split_stack">Split & Stack (Tumpuk Atas-Bawah untuk Podcast)</option>
                <option value="center_crop">Center Crop (Potong Tengah Klasik)</option>
              </select>
              <p className="form-helper">
                Algoritma pelacakan wajah cerdas menggunakan model pengenalan posisi wajah per frame.
              </p>
            </div>

            {onOpenSettings && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingTop: '0.5rem' }}>
                <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                  Gaya Font & Animasi Karaoke
                </span>
                <button
                  type="button"
                  className="btn-secondary"
                  style={{ fontSize: '0.78rem', padding: '0.35rem 0.75rem' }}
                  onClick={onOpenSettings}
                >
                  Buka Pengaturan Subtitle →
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Submit Button */}
      <button
        type="submit"
        className="btn-primary"
        disabled={busy}
        style={{ marginTop: '1.25rem', width: '100%', padding: '0.85rem 1.25rem', fontSize: '1rem', fontWeight: 600 }}
      >
        {isSubmitting ? (
          <>
            <Loader2 size={18} className="spinner" style={{ marginRight: '8px' }} />
            Mengirim Permintaan Job...
          </>
        ) : (
          <>
            <Film size={18} style={{ marginRight: '8px' }} />
            Mulai Generate Klip Video
          </>
        )}
      </button>
    </form>
  );
};
