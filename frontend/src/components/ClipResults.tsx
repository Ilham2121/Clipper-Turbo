import React, { useState } from 'react';
import type { ClipInfo } from '../api/client';
import { buildCaptionText, copyCaption, getClipDownloadUrl } from '../api/client';

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

  const handleCopy = async (clip: ClipInfo) => {
    const ok = await copyCaption(clip);
    if (ok) {
      setCopiedId(clip.id);
      window.setTimeout(() => setCopiedId((prev) => (prev === clip.id ? null : prev)), 2000);
    }
  };

  return (
    <div className="card">
      <h2 className="card-title">Klip Berhasil Dibuat</h2>
      <p className="card-description">
        Ditemukan {clips.length} klip video siap tonton dan diunduh.
      </p>

      {/* Actions on their own row: previously these shared a space-between
          header with the long ZIP button and got pushed out of view. */}
      <div className="clip-actions">
        {jobId && (
          <a
            href={`/api/jobs/${encodeURIComponent(jobId)}/download-all`}
            download={`clips-${jobId}.zip`}
            className="btn-primary btn-inline"
          >
            Download Semua Klip (.zip)
          </a>
        )}
        <button type="button" className="btn-secondary" onClick={onReset}>
          + Proses Video Lain
        </button>
      </div>

      <div className="clips-grid">
        {clips.map((clip) => {
          const downloadUrl = clip.downloadUrl || getClipDownloadUrl(clip.id);
          const is916 = clip.format === '9:16';
          const duration = clip.segmentEnd - clip.segmentStart;

          return (
            <div key={clip.id} className={`clip-card ${is916 ? 'ratio-9-16' : ''}`}>
              <div className="clip-video-container">
                <video
                  controls
                  preload="metadata"
                  src={downloadUrl}
                  playsInline
                >
                  Browser Anda tidak mendukung tag video.
                </video>
              </div>

              <div className="clip-details">
                <div className="clip-meta-row">
                  <span className="badge badge-ratio">{clip.format}</span>
                  <span className="badge-duration">
                    {formatDuration(clip.segmentStart)} - {formatDuration(clip.segmentEnd)} ({Math.round(duration)}s)
                  </span>
                </div>

                {clip.viralityScore != null && (
                  <div className="clip-meta-row">
                    <span className="badge badge-ratio">Viral {clip.viralityScore}/100</span>
                    {clip.hook && <span className="badge-duration">{clip.hook}</span>}
                  </div>
                )}

                {buildCaptionText(clip) && (
                  <button type="button" className="btn-secondary" onClick={() => handleCopy(clip)}>
                    {copiedId === clip.id ? 'Tersalin!' : 'Salin Caption & Tagar'}
                  </button>
                )}

                {clip.subtitleUrl && (
                  <a
                    href={clip.subtitleUrl}
                    download={`clip-${clip.id}-${clip.format.replace(':', 'x')}.srt`}
                    className="btn-secondary"
                    style={{ textAlign: 'center', textDecoration: 'none' }}
                  >
                    Unduh Subtitle (.srt)
                  </a>
                )}

                <a
                  href={downloadUrl}
                  download={`clip-${clip.id}-${clip.format.replace(':', 'x')}.mp4`}
                  className="btn-download"
                >
                  Unduh Klip (.mp4)
                </a>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
