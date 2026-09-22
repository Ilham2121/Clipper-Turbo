import React, { useEffect, useRef, useState } from 'react';
import { parseTimeToSeconds, type ManualInputRow } from './manualSegments';

interface ManualTimelineProps {
  url: string;
  rows: ManualInputRow[];
  onUpdateRow: (id: string, field: keyof ManualInputRow, val: string) => void;
}

declare global {
  interface Window {
    YT?: any;
    onYouTubeIframeAPIReady?: () => void;
  }
}

function extractVideoId(url: string): string | null {
  const trimmed = url.trim();
  let m = trimmed.match(/[?&]v=([A-Za-z0-9_-]{11})/);
  if (m) return m[1];
  m = trimmed.match(/youtu\.be\/([A-Za-z0-9_-]{11})/);
  if (m) return m[1];
  m = trimmed.match(/youtube\.com\/(shorts|embed|v)\/([A-Za-z0-9_-]{11})/);
  if (m) return m[2];
  return null;
}

function formatSeconds(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${r.toString().padStart(2, '0')}`;
  return `${m.toString().padStart(2, '0')}:${r.toString().padStart(2, '0')}`;
}

let ytApiPromise: Promise<void> | null = null;
function loadYouTubeApi(): Promise<void> {
  if (typeof window === 'undefined') return Promise.reject(new Error('no window'));
  if (window.YT?.Player) return Promise.resolve();
  if (ytApiPromise) return ytApiPromise;
  ytApiPromise = new Promise((resolve, reject) => {
    const prev = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      if (prev) prev();
      resolve();
    };
    const tag = document.createElement('script');
    tag.src = 'https://www.youtube.com/iframe_api';
    tag.onerror = () => {
      ytApiPromise = null;
      reject(new Error('YouTube IFrame API failed to load'));
    };
    document.head.appendChild(tag);
  });
  return ytApiPromise;
}

export const ManualTimeline: React.FC<ManualTimelineProps> = ({ url, rows, onUpdateRow }) => {
  const videoId = extractVideoId(url);
  const playerRef = useRef<any>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [duration, setDuration] = useState<number>(0);
  const [apiFailed, setApiFailed] = useState<boolean>(false);

  useEffect(() => {
    if (!videoId || !containerRef.current) return;
    let cancelled = false;
    setDuration(0);
    setApiFailed(false);

    loadYouTubeApi()
      .then(() => {
        if (cancelled || !containerRef.current) return;
        if (playerRef.current?.destroy) {
          try { playerRef.current.destroy(); } catch { /* ignore */ }
          playerRef.current = null;
        }
        playerRef.current = new window.YT.Player(containerRef.current, {
          height: '100%',
          width: '100%',
          videoId,
          playerVars: { rel: 0 },
          events: {
            onReady: (e: any) => {
              if (!cancelled) {
                const d = e.target?.getDuration?.() ?? 0;
                if (d > 0) setDuration(d);
              }
            },
          },
        });
      })
      .catch(() => {
        if (!cancelled) setApiFailed(true);
      });

    return () => {
      cancelled = true;
    };
  }, [videoId]);

  useEffect(() => {
    return () => {
      if (playerRef.current?.destroy) {
        try { playerRef.current.destroy(); } catch { /* ignore */ }
        playerRef.current = null;
      }
    };
  }, []);

  if (!videoId || apiFailed) return null;

  const maxSlider = duration > 0
    ? Math.ceil(duration)
    : Math.max(300, ...rows.map((r) => Math.ceil(parseTimeToSeconds(r.end) || 0)));

  const handlePlay = (row: ManualInputRow) => {
    const start = parseTimeToSeconds(row.start);
    const end = parseTimeToSeconds(row.end);
    if (!playerRef.current?.loadVideoById || !videoId) return;
    if (end > start) {
      playerRef.current.loadVideoById({ videoId, startSeconds: start, endSeconds: end });
    } else {
      playerRef.current.loadVideoById({ videoId, startSeconds: start });
    }
  };

  return (
    <div className="timeline-block">
      <div className="timeline-player">
        <div ref={containerRef} />
      </div>
      {rows.map((row) => {
        const startSec = parseTimeToSeconds(row.start);
        const endSec = Math.max(parseTimeToSeconds(row.end), startSec);
        return (
          <div key={row.id} className="timeline-row">
            <div className="timeline-sliders">
              <input
                type="range"
                min={0}
                max={maxSlider}
                step={1}
                value={Math.min(startSec, maxSlider)}
                onChange={(e) => onUpdateRow(row.id, 'start', formatSeconds(Number(e.target.value)))}
                aria-label={`Start segmen ${row.label || ''}`}
              />
              <input
                type="range"
                min={0}
                max={maxSlider}
                step={1}
                value={Math.min(endSec, maxSlider)}
                onChange={(e) => onUpdateRow(row.id, 'end', formatSeconds(Number(e.target.value)))}
                aria-label={`End segmen ${row.label || ''}`}
              />
            </div>
            <button type="button" className="btn-secondary" onClick={() => handlePlay(row)}>
              Putar
            </button>
          </div>
        );
      })}
      <p className="form-helper">Geser handle untuk mengisi kolom waktu, lalu tekan Putar untuk pratinjau segmen.</p>
    </div>
  );
};
