import React, { useEffect, useRef, useState } from 'react';
import type { EstimateResponse, JobStatusResponse } from '../api/client';
import { formatEstimate, getEstimate, loadLogVisible, saveLogVisible } from '../api/client';

interface JobProgressProps {
  status: JobStatusResponse;
  onReset: () => void;
  youtubeUrl?: string;
}

const STAGES = [
  { key: 'downloading', label: '1. Unduh Video' },
  { key: 'transcribing', label: '2. Transkripsi' },
  { key: 'selecting_segments', label: '3. Pilih Momen' },
  { key: 'rendering', label: '4. Render Klip' },
  { key: 'completed', label: '5. Selesai' },
];

export const JobProgress: React.FC<JobProgressProps> = ({ status, onReset, youtubeUrl }) => {
  const currentStageIndex = STAGES.findIndex((s) => s.key === status.status);
  const isFailed = status.status === 'failed';
  const isCompleted = status.status === 'completed';
  const [logVisible, setLogVisible] = useState<boolean>(() => loadLogVisible());
  const [collapsed, setCollapsed] = useState<boolean>(false);
  const logEndRef = useRef<HTMLDivElement>(null);
  const logs = status.progressLog ?? [];
  const [estimate, setEstimate] = useState<EstimateResponse | null>(null);
  const [startTime, setStartTime] = useState<number | null>(null);
  const [now, setNow] = useState<number | null>(null);

  useEffect(() => {
    const t = Date.now();
    setStartTime(t);
    setNow(t);
    const id = window.setInterval(() => setNow(Date.now()), 5000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (!youtubeUrl || isCompleted || isFailed) return;
    let cancelled = false;
    void getEstimate(youtubeUrl).then((res) => {
      if (!cancelled) setEstimate(res);
    });
    return () => {
      cancelled = true;
    };
  }, [youtubeUrl, isCompleted, isFailed]);

  const elapsedSec = startTime !== null && now !== null ? Math.max(0, Math.floor((now - startTime) / 1000)) : null;
  const remainingSec = estimate && elapsedSec !== null ? Math.max(0, estimate.estimatedSeconds - elapsedSec) : null;

  useEffect(() => {
    if (logVisible) {
      logEndRef.current?.scrollIntoView({ block: 'nearest' });
    }
  }, [logs.length, logVisible]);

  const toggleLog = () => {
    setLogVisible((prev) => {
      saveLogVisible(!prev);
      return !prev;
    });
  };

  const getStageClass = (index: number) => {
    if (isFailed) {
      if (index === currentStageIndex) return 'stage-step failed';
      if (index < currentStageIndex) return 'stage-step completed';
      return 'stage-step';
    }
    if (isCompleted) {
      return 'stage-step completed';
    }
    if (index === currentStageIndex) {
      return 'stage-step active';
    }
    if (index < currentStageIndex) {
      return 'stage-step completed';
    }
    return 'stage-step';
  };

  const getStatusMessage = () => {
    switch (status.status) {
      case 'queued':
        return 'Job sedang antre dalam antrean pemrosesan...';
      case 'downloading':
        return 'Mengunduh stream video dan audio dari YouTube...';
      case 'transcribing':
        return 'Mengekstrak subtitle atau menjalankan speech recognition...';
      case 'selecting_segments':
        return 'Menganalisis konten untuk memilih momen klip terbaik...';
      case 'rendering':
        return 'Memotong video, burn-in subtitle, dan konversi rasio aspek...';
      case 'completed':
        return 'Seluruh klip berhasil dibuat dan siap diunduh!';
      case 'failed':
        return 'Proses terhenti karena terjadi kesalahan.';
      default:
        return 'Sedang memproses...';
    }
  };

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <div>
          <h2 className="card-title">Progres Pemrosesan Video</h2>
          <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            Job ID: {status.jobId}
          </span>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button type="button" className="btn-secondary" onClick={() => setCollapsed((v) => !v)}>
            {collapsed ? 'Tampilkan Detail' : 'Sembunyikan Detail'}
          </button>
          {/* Always available: results live below, and without this the finished
              state had no way to clear the panel (bug). */}
          <button type="button" className="btn-secondary" onClick={onReset}>
            {isFailed ? 'Coba Lagi' : 'Bersihkan'}
          </button>
        </div>
      </div>

      {collapsed && (
        <p className="form-helper" style={{ marginBottom: 0 }}>
          {getStatusMessage()} · {Math.round(status.progressPercent ?? 0)}% selesai
        </p>
      )}

      {!collapsed && (<>

      {/* Warning Banner (EC-2) */}
      {status.warning && (
        <div className="banner banner-warning">
          <strong>Peringatan:</strong> {status.warning}
        </div>
      )}

      {/* Error Banner (AC-10) */}
      {isFailed && (
        <div className="banner banner-error" style={{ flexDirection: 'column', gap: '0.25rem' }}>
          <strong>Pemrosesan Gagal:</strong>
          <span>
            {typeof status.error === 'object' && status.error !== null
              ? `${status.error.stage ? `[${status.error.stage}] ` : ''}${status.error.message}`
              : (status.error || 'Terjadi kesalahan sistem yang tidak diketahui.')}
          </span>
        </div>
      )}

      {/* Status Description */}
      <p style={{ fontSize: '0.9rem', color: isFailed ? 'var(--status-error)' : 'var(--text-secondary)', marginBottom: '0.75rem' }}>
        {getStatusMessage()}
      </p>

      {/* Processing-time estimate (heuristic, best-effort) */}
      {!isFailed && estimate && (
        <div className="banner banner-info">
          <span>
            Estimasi proses: <strong>{formatEstimate(estimate.estimatedSeconds)}</strong>
            {remainingSec !== null && !isCompleted && (
              <> (sisa ±{formatEstimate(remainingSec).replace('±', '')})</>
            )}
            {isCompleted && elapsedSec !== null && <> — selesai dalam {formatEstimate(elapsedSec).replace('±', '')}</>}
            <br />
            <span style={{ fontSize: '0.78rem' }}>
              Video {Math.round(estimate.duration / 60)} mnt
              {estimate.hardwareAccelerated ? ' · akselerasi GPU' : ' · render CPU'} ·
              unduh ±{estimate.breakdown.download}d / transkrip ±{estimate.breakdown.transcribe}d /
              render ±{estimate.breakdown.render}d. Angka perkiraan, bisa berbeda.
            </span>
          </span>
        </div>
      )}

      {/* Visual Stepper Track */}
      <div className="stepper-container">
        <div className="stepper-track">
          <div
            className="stepper-fill"
            style={{
              width: `${Math.min(Math.max(status.progressPercent ?? 0, 5), 100)}%`,
              backgroundColor: isFailed ? 'var(--status-error)' : undefined,
              backgroundImage: isFailed ? 'none' : undefined,
            }}
          />
        </div>

        {/* 5 Stages Grid */}
        <div className="stages-grid">
          {STAGES.map((st, idx) => (
            <div key={st.key} className={getStageClass(idx)}>
              {st.label}
            </div>
          ))}
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
        <span>Status: <strong style={{ color: 'var(--text-primary)' }}>{status.status}</strong></span>
        <span>{Math.round(status.progressPercent ?? 0)}% Selesai</span>
      </div>

      {/* Progress Log (collapsible, preference persisted) */}
      <div className="progress-log-block">
        <button type="button" className="btn-secondary" onClick={toggleLog}>
          {logVisible ? 'Sembunyikan Log Progres' : 'Tampilkan Log Progres'}
        </button>
        {logVisible && (
          <div className="progress-log">
            {logs.length === 0 ? (
              <span className="progress-log-empty">Menunggu pesan progres pertama...</span>
            ) : (
              logs.map((entry, idx) => (
                <div key={`${entry.t}-${idx}`} className="progress-log-line">
                  <span className="progress-log-time">{entry.t}</span>
                  <span>{entry.msg}</span>
                </div>
              ))
            )}
            <div ref={logEndRef} />
          </div>
        )}
      </div>
      </>)}
    </div>
  );
};
