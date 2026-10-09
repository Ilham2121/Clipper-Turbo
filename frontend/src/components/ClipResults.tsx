import React, { useState } from 'react';
import {
  Download,
  Copy,
  Check,
  FileText,
  Sparkles,
  RotateCcw,
  Film,
} from 'lucide-react';
import type { ClipInfo } from '../api/client';
import { buildCaptionText, copyCaption, getClipDownloadUrl } from '../api/client';
import { useToast } from './Toast';

interface ClipResultsProps {
  clips: ClipInfo[];
  onReset: () => void;
  jobId?: string;
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const mm = m.toString().padStart(2, '0');
  const ss = s.toString().padStart(2, '0');
  return `${mm}:${ss}`;
}

export const ClipResults: React.FC<ClipResultsProps> = ({ clips, onReset, jobId }) => {
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const toast = useToast();

  const handleCopy = async (clip: ClipInfo) => {
    const ok = await copyCaption(clip);
    if (ok) {
      setCopiedId(clip.id);
      toast.success('Caption & Tagar Disalin', 'Teks caption siap dipaste ke media sosial.');
      window.setTimeout(() => setCopiedId((prev) => (prev === clip.id ? null : prev)), 2000);
    } else {
      toast.error('Gagal Menyalin Teks');
    }
  };

  return (
    <div className="card" style={{ animation: 'fadeIn 0.25s ease-out' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.25rem' }}>
        <h2 className="card-title" style={{ margin: 0 }}>
          Galeri Hasil Klip Video
        </h2>
        <span className="badge-pill badge-pill-hw">
          <Film size={12} /> {clips.length} Klip Siap
        </span>
      </div>
      <p className="card-description">
        Klip video telah selesai dipotong, di-reframe, dan dilengkapi subtitle animasi per kata.
      </p>

      {/* Action Header Banner */}
      <div className="clip-actions" style={{ marginTop: '1rem', marginBottom: '1.5rem', flexWrap: 'wrap' }}>
        {jobId && (
          <a
            href={`/api/jobs/${encodeURIComponent(jobId)}/download-all`}
            download={`clips-${jobId}.zip`}
            className="btn-primary btn-inline"
            style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem' }}
          >
            <Download size={16} /> Download Semua Klip (.zip)
          </a>
        )}
        <button
          type="button"
          className="btn-secondary"
          onClick={onReset}
          style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem' }}
        >
          <RotateCcw size={15} /> Buat Job Klip Baru
        </button>
      </div>

      {/* Grid of Generated Clips */}
      <div className="clips-grid">
        {clips.map((clip) => {
          const downloadUrl = clip.downloadUrl || getClipDownloadUrl(clip.id);
          const is916 = clip.format === '9:16';
          const duration = clip.segmentEnd - clip.segmentStart;
          const score = clip.viralityScore;

          return (
            <div key={clip.id} className={`clip-card ${is916 ? 'ratio-9-16' : ''}`}>
              {/* Video Player */}
              <div className="clip-video-container" style={{ position: 'relative' }}>
                <video
                  controls
                  preload="metadata"
                  src={downloadUrl}
                  playsInline
                >
                  Browser tidak mendukung tag video.
                </video>
              </div>

              {/* Clip Details & Controls */}
              <div className="clip-details">
                <div className="clip-meta-row" style={{ justifyContent: 'space-between' }}>
                  <span className={`badge ${is916 ? 'badge-ratio' : 'badge-duration'}`}>
                    {is916 ? '9:16 Shorts/TikTok' : '16:9 Landscape'}
                  </span>
                  <span className="badge-duration">
                    {formatDuration(clip.segmentStart)} - {formatDuration(clip.segmentEnd)} ({Math.round(duration)}s)
                  </span>
                </div>

                {score != null && (
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.5rem',
                      padding: '0.4rem 0.6rem',
                      background:
                        score >= 80
                          ? 'rgba(16, 185, 129, 0.12)'
                          : score >= 60
                          ? 'rgba(99, 102, 241, 0.12)'
                          : 'rgba(255, 255, 255, 0.05)',
                      borderRadius: 'var(--radius-sm)',
                      border: `1px solid ${
                        score >= 80
                          ? 'rgba(16, 185, 129, 0.3)'
                          : score >= 60
                          ? 'rgba(99, 102, 241, 0.3)'
                          : 'rgba(255, 255, 255, 0.08)'
                      }`,
                    }}
                  >
                    <Sparkles
                      size={14}
                      color={score >= 80 ? 'var(--status-success)' : 'var(--accent-primary)'}
                    />
                    <strong
                      style={{
                        fontSize: '0.8rem',
                        color: score >= 80 ? '#6ee7b7' : '#a5b4fc',
                      }}
                    >
                      Virality {score}/100
                    </strong>
                    {clip.hook && (
                      <span
                        style={{
                          fontSize: '0.72rem',
                          color: 'var(--text-muted)',
                          marginLeft: 'auto',
                          maxWidth: '140px',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                        title={clip.hook}
                      >
                        {clip.hook}
                      </span>
                    )}
                  </div>
                )}

                {/* Copy Caption button */}
                {buildCaptionText(clip) && (
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => handleCopy(clip)}
                    style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem' }}
                  >
                    {copiedId === clip.id ? (
                      <>
                        <Check size={14} color="var(--status-success)" /> Tersalin!
                      </>
                    ) : (
                      <>
                        <Copy size={14} /> Salin Caption & Tagar
                      </>
                    )}
                  </button>
                )}

                {/* Subtitle Sidecar Download */}
                {clip.subtitleUrl && (
                  <a
                    href={clip.subtitleUrl}
                    download={`clip-${clip.id}-${clip.format.replace(':', 'x')}.srt`}
                    className="btn-secondary"
                    style={{
                      textAlign: 'center',
                      textDecoration: 'none',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '0.5rem',
                    }}
                  >
                    <FileText size={14} /> Unduh Subtitle (.srt)
                  </a>
                )}

                {/* Primary Clip Download */}
                <a
                  href={downloadUrl}
                  download={`clip-${clip.id}-${clip.format.replace(':', 'x')}.mp4`}
                  className="btn-download"
                  style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem' }}
                >
                  <Download size={15} /> Unduh Klip (.mp4)
                </a>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
