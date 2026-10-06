import React, { useState } from 'react';
import type { AspectRatio, CreateJobFormData, ManualSegment } from '../api/client';
import { LLM_PRESETS } from '../api/client';
import { LlmKeyManager } from './LlmKeyManager';
import { ManualTimeline } from './ManualTimeline';

interface JobFormProps {
  onSubmit: (payload: CreateJobFormData) => Promise<void>;
  isSubmitting: boolean;
  /** True while another job is still running (submit blocked, form stays visible). */
  disabled?: boolean;
}

import type { ManualInputRow } from './manualSegments';
import { parseTimeToSeconds } from './manualSegments';

export const JobForm: React.FC<JobFormProps> = ({ onSubmit, isSubmitting, disabled }) => {
  const [url, setUrl] = useState('');
  const [mode, setMode] = useState<'auto' | 'manual'>('auto');
  
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

  const [formError, setFormError] = useState<string | null>(null);

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
      setFormError('Masih ada job berjalan. Tunggu selesai atau hentikan job tersebut dulu.');
      return;
    }

    // Validasi URL
    const trimmedUrl = url.trim();
    if (!trimmedUrl) {
      setFormError('Masukkan URL video YouTube terlebih dahulu.');
      return;
    }
    if (!trimmedUrl.includes('youtube.com') && !trimmedUrl.includes('youtu.be')) {
      setFormError('URL harus berasal dari YouTube (contoh: https://www.youtube.com/watch?v=... atau https://youtu.be/...)');
      return;
    }

    // Validasi Aspect Ratios
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
    };

    if (mode === 'auto') {
      if (!hasStoredKey) {
        setFormError('Simpan API Key untuk provider ini terlebih dahulu (atau isi lalu klik Simpan).');
        return;
      }
      formData.llmProvider = provider;
      if (model.trim()) formData.llmModel = model.trim();
      if (provider === 'custom' && customEndpoint.trim()) {
        formData.llmBaseUrl = customEndpoint.trim();
      }
    } else {
      // Parse manual segments
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
      <h2 className="card-title">Konfigurasi Pembuatan Klip</h2>
      <p className="card-description">
        Masukkan URL video YouTube dan tentukan metode penentuan segmen klip.
      </p>

      {formError && (
        <div className="banner banner-error">
          <span>{formError}</span>
        </div>
      )}

      {disabled && (
        <div className="banner banner-info">
          <span>Ada job yang sedang berjalan — lihat panel proses di bawah halaman ini.</span>
        </div>
      )}

      {/* URL Input */}
      <div className="form-group">
        <label className="form-label" htmlFor="yt-url">
          URL Video YouTube
        </label>
        <input
          id="yt-url"
          type="url"
          className="input-text"
          placeholder="https://www.youtube.com/watch?v=..."
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          required
          disabled={busy}
        />
        <p className="form-helper">Mendukung link reguler maupun youtu.be shortlinks.</p>
      </div>

      {/* Mode Toggle (AC-11) */}
      <div className="form-group">
        <label className="form-label">Mode Pemilihan Segmen</label>
        <div className="segmented-control">
          <button
            type="button"
            className={`segment-btn ${mode === 'auto' ? 'active' : ''}`}
            onClick={() => setMode('auto')}
            disabled={busy}
          >
            Otomatis (AI LLM)
          </button>
          <button
            type="button"
            className={`segment-btn ${mode === 'manual' ? 'active' : ''}`}
            onClick={() => setMode('manual')}
            disabled={busy}
          >
            Manual (Timestamp)
          </button>
        </div>
      </div>

      {/* Section Mode Otomatis */}
      {mode === 'auto' && (
        <div className="form-group" style={{ padding: '1rem', background: 'var(--bg-input)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)' }}>
          <div className="form-group">
            <label className="form-label" htmlFor="llm-provider">
              Pilihan Provider LLM
            </label>
            <select
              id="llm-provider"
              className="select-input"
              value={provider}
              onChange={handleProviderChange}
              disabled={busy}
            >
              {Object.entries(LLM_PRESETS).map(([key, info]) => (
                <option key={key} value={key}>
                  {info.label}
                </option>
              ))}
            </select>
          </div>

          {currentPreset && (
            <div className="banner banner-info" style={{ marginBottom: '1rem' }}>
              <span>{currentPreset.tip}</span>
            </div>
          )}

          <div className="form-group">
            <label className="form-label" htmlFor="llm-model">
              Nama Model
            </label>
            <input
              id="llm-model"
              type="text"
              className="input-text input-mono"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="e.g. meta-llama/llama-3.3-70b-instruct:free"
              disabled={busy}
            />
          </div>

          <LlmKeyManager
            key={provider}
            provider={provider}
            model={model}
            customEndpoint={customEndpoint}
            disabled={busy}
            onHasKeyChange={setHasStoredKey}
          />

          {provider === 'custom' && (
            <div className="form-group">
              <label className="form-label" htmlFor="custom-endpoint">
                Custom Endpoint URL
              </label>
              <input
                id="custom-endpoint"
                type="url"
                className="input-text input-mono"
                value={customEndpoint}
                onChange={(e) => setCustomEndpoint(e.target.value)}
                placeholder="http://localhost:11434/v1"
                disabled={busy}
              />
            </div>
          )}
        </div>
      )}

      {/* Section Mode Manual */}
      {mode === 'manual' && (
        <div className="form-group" style={{ padding: '1rem', background: 'var(--bg-input)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)' }}>
          <label className="form-label">Daftar Rentang Waktu Klip</label>
          <p className="form-helper" style={{ marginBottom: '0.75rem' }}>
            Format waktu bisa berupa detik (misal: <code>45</code>) atau format menit:detik (misal: <code>01:15</code>).
          </p>

          <ManualTimeline url={url} rows={manualRows} onUpdateRow={handleUpdateSegmentRow} />

          {manualRows.map((row) => (
            <div key={row.id} className="segment-row">
              <input
                type="text"
                className="input-text input-mono"
                placeholder="Start (00:30)"
                value={row.start}
                onChange={(e) => handleUpdateSegmentRow(row.id, 'start', e.target.value)}
                disabled={busy}
                style={{ maxWidth: '140px' }}
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
                style={{ maxWidth: '140px' }}
                required
              />
              <input
                type="text"
                className="input-text"
                placeholder="Label / Judul Klip"
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
                  title="Hapus baris segmen"
                >
                  Hapus
                </button>
              )}
            </div>
          ))}

          <button
            type="button"
            className="btn-secondary"
            onClick={handleAddSegmentRow}
            disabled={busy}
            style={{ marginTop: '0.5rem' }}
          >
            + Tambah Segmen Klip
          </button>
        </div>
      )}

      {/* Aspect Ratio & Options */}
      <div className="form-group">
        <label className="form-label">Format Output & Rasio</label>
        <div className="options-grid">
          <label className={`option-checkbox-card ${ratio916 ? 'selected' : ''}`}>
            <input
              type="checkbox"
              checked={ratio916}
              onChange={(e) => setRatio916(e.target.checked)}
              disabled={busy}
            />
            <div>
              <strong>9:16 Vertikal</strong>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                Shorts, Reels, TikTok (Smart Reframe)
              </div>
            </div>
          </label>

          <label className={`option-checkbox-card ${ratio169 ? 'selected' : ''}`}>
            <input
              type="checkbox"
              checked={ratio169}
              onChange={(e) => setRatio169(e.target.checked)}
              disabled={busy}
            />
            <div>
              <strong>16:9 Horizontal</strong>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                YouTube Standar / Landscape
              </div>
            </div>
          </label>
        </div>
      </div>

      {/* Burn Subtitles Toggle */}
      <div className="form-group">
        <label className={`option-checkbox-card ${burnSubtitles ? 'selected' : ''}`}>
          <input
            type="checkbox"
            checked={burnSubtitles}
            onChange={(e) => setBurnSubtitles(e.target.checked)}
            disabled={busy}
          />
          <div>
            <strong>Burn-in Subtitles ke Video</strong>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
              Menempelkan subtitle otomatis langsung di dalam frame video klip.
            </div>
          </div>
        </label>
      </div>

      {/* Submit Button */}
      <p className="form-helper" style={{ marginTop: '1rem', marginBottom: '0.5rem' }}>
        Setelah job dibuat, halaman progres menampilkan estimasi waktu proses (perkiraan berdasarkan durasi video).
      </p>
      <button
        type="submit"
        className="btn-primary"
        disabled={busy}
      >
        {isSubmitting ? 'Mengirim Permintaan...' : 'Mulai Proses Generate Klip'}
      </button>
    </form>
  );
};
