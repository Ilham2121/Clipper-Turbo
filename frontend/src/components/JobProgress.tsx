import React, { useEffect, useRef, useState } from 'react';
import type { JobStatusResponse } from '../api/client';
import { formatEstimateRange, loadLogVisible, saveLogVisible } from '../api/client';

interface JobProgressProps {
  status: JobStatusResponse;
  onReset: () => void;
  youtubeUrl?: string;
}

const STAGES = [
  { key: 'downloading', label: '1. Unduh Video' },
  { key: 'transcribing', label: '2. Transkripsi ASR' },
  { key: 'selecting_segments', label: '3. Pilih Momen' },
  { key: 'rendering', label: '4. Render Klip' },
  { key: 'completed', label: '5. Selesai' },
];

const STATUS_LABELS: Record<string, string> = {
  queued: 'Dalam Antrean',
  downloading: 'Mengunduh Video',
  transcribing: 'Transkripsi Suara (ASR)',
  selecting_segments: 'Menganalisis Momen Viral',
  rendering: 'Merender Klip & Subtitle',
  completed: 'Selesai',
  failed: 'Gagal',
};

export const JobProgress: React.FC<JobProgressProps> = ({ status, onReset }) => {
  const currentStageIndex = STAGES.findIndex((s) => s.key === status.status);
  const isFailed = status.status === 'failed';
  const isCompleted = status.status === 'completed';
  const [logVisible, setLogVisible] = useState<boolean>(() => loadLogVisible());
  const [collapsed, setCollapsed] = useState<boolean>(false);
  const logEndRef = useRef<HTMLDivElement>(null);
  const logs = status.progressLog ?? [];

  // Elapsed time calculated relative to server timestamp, ticked locally
  const [elapsedSec, setElapsedSec] = useState<number>(0);

  useEffect(() => {
    const startStr = status.startedAt || status.createdAt;
    if (!startStr) return;
    const startTs = new Date(startStr).getTime();
    const serverTs = status.serverNow ? new Date(status.serverNow).getTime() : Date.now();
    const initialElapsed = Math.max(0, Math.floor((serverTs - startTs) / 1000));
    setElapsedSec(initialElapsed);

    if (isCompleted || isFailed) return;

    const interval = window.setInterval(() => {
      setElapsedSec((prev) => prev + 1);
    }, 1000);
    return () => window.clearInterval(interval);
  }, [status.startedAt, status.createdAt, status.serverNow, isCompleted, isFailed]);

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
        return 'Job sedang menunggu antrean pemrosesan...';
      case 'downloading':
        return 'Mengunduh stream video dan audio dari YouTube...';
      case 'transcribing':
        return 'Mengekstrak subtitle atau menjalankan speech recognition (ASR)...';
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

  // ETA text and overrun state
  const hasEta = status.etaSeconds !== undefined && status.etaSeconds !== null;
  const isOverrun = !isCompleted && !isFailed && hasEta && status.etaHighSeconds !== undefined && status.etaHighSeconds !== null && elapsedSec > status.etaHighSeconds;

  const formatSecondsDuration = (sec: number) => {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    if (m === 0) return `${s} dtk`;
    return `${m} mnt ${s} dtk`;
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

      {!collapsed && (
        <>
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

          {/* Reliable Processing ETA & Stage Timings Banner */}
          {!isFailed && (
            <div className="banner banner-info" style={{ flexDirection: 'column', gap: '0.4rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%', flexWrap: 'wrap', gap: '0.5rem' }}>
                <div>
                  {!isCompleted && hasEta && (
                    <span>
                      Estimasi sisa waktu: <strong>{formatEstimateRange(status.etaSeconds, status.etaLowSeconds, status.etaHighSeconds)}</strong>
                    </span>
                  )}
                  {isCompleted && (
                    <span>
                      Total waktu proses: <strong>{formatSecondsDuration(elapsedSec)}</strong>
                    </span>
                  )}
                </div>
                <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                  Waktu berjalan: <strong>{formatSecondsDuration(elapsedSec)}</strong>
                </div>
              </div>

              {isOverrun && (
                <div style={{ fontSize: '0.78rem', color: 'var(--status-warning)' }}>
                  Memakan waktu sedikit lebih lama dari perkiraan awal. Menyesuaikan pemrosesan...
                </div>
              )}
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

            {/* 5 Stages Grid with Timing Badges */}
            <div className="stages-grid">
              {STAGES.map((st, idx) => {
                const timing = (status.stageTimings || []).find((t) => t.stage === st.key);
                return (
                  <div key={st.key} className={getStageClass(idx)} style={{ position: 'relative' }}>
                    <span>{st.label}</span>
                    {timing && (
                      <span
                        style={{
                          fontSize: '0.7rem',
                          marginLeft: '0.35rem',
                          opacity: 0.8,
                          fontFamily: 'var(--font-mono)',
                        }}
                      >
                        ({timing.seconds.toFixed(0)}s)
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
            <span>
              Status: <strong style={{ color: 'var(--text-primary)' }}>{STATUS_LABELS[status.status] || status.status}</strong>
            </span>
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
        </>
      )}
    </div>
  );
};
