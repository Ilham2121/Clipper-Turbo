import React, { useRef } from 'react';
import { Scissors, Trash2 } from 'lucide-react';
import type { ClipProject, EditorLayer } from './types';
import type { ProjectAction } from './useProject';

interface TimelineProps {
  project: ClipProject;
  masterDuration: number;
  currentTime: number;
  splitMarks: number[];
  selectedCutRange: [number, number] | null;
  selectedLayerId: string | null;
  onSeek: (t: number) => void;
  onAddSplitAtPlayhead: () => void;
  onSelectCutRange: (range: [number, number] | null) => void;
  onDeleteSelectedRange: () => void;
  onSelectLayer: (id: string | null) => void;
  dispatch: React.Dispatch<ProjectAction>;
}

export const Timeline: React.FC<TimelineProps> = ({
  project,
  masterDuration,
  currentTime,
  splitMarks,
  selectedCutRange,
  selectedLayerId,
  onSeek,
  onAddSplitAtPlayhead,
  onSelectCutRange,
  onDeleteSelectedRange,
  onSelectLayer,
  dispatch,
}) => {
  const rulerRef = useRef<HTMLDivElement | null>(null);
  const dur = Math.max(1, masterDuration);
  const pct = (t: number) => `${Math.max(0, Math.min(100, (t / dur) * 100))}%`;

  const clientXToTime = (clientX: number): number => {
    const el = rulerRef.current;
    if (!el) return currentTime;
    const rect = el.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    return Math.round(ratio * dur * 100) / 100;
  };

  const startScrub = (e: React.PointerEvent) => {
    onSeek(clientXToTime(e.clientX));
    const onMove = (ev: PointerEvent) => onSeek(clientXToTime(ev.clientX));
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  const startTrimHandle = (e: React.PointerEvent, edge: 'in' | 'out') => {
    e.stopPropagation();
    const onMove = (ev: PointerEvent) => {
      const t = clientXToTime(ev.clientX);
      if (edge === 'in') {
        const nextIn = Math.max(0, Math.min(project.trim.out - 1.0, t));
        dispatch({
          type: 'SET_TRIM',
          trim: {
            ...project.trim,
            in: nextIn,
            cuts: project.trim.cuts.filter(([a]) => a >= nextIn),
          },
        });
      } else {
        const nextOut = Math.min(dur, Math.max(project.trim.in + 1.0, t));
        dispatch({
          type: 'SET_TRIM',
          trim: {
            ...project.trim,
            out: nextOut,
            cuts: project.trim.cuts.filter(([, b]) => b <= nextOut),
          },
        });
      }
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  const startLayerEdgeDrag = (
    e: React.PointerEvent,
    layer: EditorLayer,
    edge: 'start' | 'end',
  ) => {
    e.stopPropagation();
    const onMove = (ev: PointerEvent) => {
      const t = clientXToTime(ev.clientX);
      const curEnd = layer.end ?? project.trim.out;
      if (edge === 'start') {
        const nextStart = Math.max(0, Math.min(curEnd - 0.3, t));
        dispatch({
          type: 'UPDATE_LAYER',
          id: layer.id,
          patch: { start: nextStart },
        });
      } else {
        const nextEnd = Math.min(dur, Math.max(layer.start + 0.3, t));
        dispatch({
          type: 'UPDATE_LAYER',
          id: layer.id,
          patch: { end: nextEnd },
        });
      }
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  // Build clickable sub-segments between trim.in, splitMarks, and trim.out
  const splitBoundaries = [
    project.trim.in,
    ...splitMarks.filter((m) => m > project.trim.in + 0.05 && m < project.trim.out - 0.05),
    project.trim.out,
  ].sort((a, b) => a - b);

  const titleLayers = project.layers.filter((l) => l.type === 'title');
  const overlayLayers = project.layers.filter((l) => l.type !== 'title');

  return (
    <div className="ce-timeline">
      {/* Timeline toolbar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0.35rem 0.85rem',
          borderBottom: '1px solid var(--border)',
          fontSize: '0.76rem',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <button
            type="button"
            className="btn-secondary"
            style={{ padding: '0.2rem 0.55rem', fontSize: '0.75rem' }}
            onClick={onAddSplitAtPlayhead}
            title="Pisah segmen di posisi playhead (Shortcut: S)"
          >
            <Scissors size={12} /> Split (S)
          </button>
          {selectedCutRange && (
            <button
              type="button"
              className="btn-secondary"
              style={{
                padding: '0.2rem 0.55rem',
                fontSize: '0.75rem',
                color: 'var(--danger)',
                borderColor: 'var(--danger)',
              }}
              onClick={onDeleteSelectedRange}
            >
              <Trash2 size={12} /> Potong Segmen ({selectedCutRange[0].toFixed(1)}s–
              {selectedCutRange[1].toFixed(1)}s)
            </button>
          )}
        </div>
        <span style={{ color: 'var(--fg-muted)', fontFamily: 'var(--font-mono)' }}>
          Master: {currentTime.toFixed(2)}s / {dur.toFixed(2)}s
        </span>
      </div>

      {/* Tracks area */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '76px 1fr',
          gap: '0.35rem 0.5rem',
          padding: '0.5rem 0.85rem',
          alignItems: 'center',
          overflowY: 'auto',
        }}
      >
        {/* Video track */}
        <span style={{ fontSize: '0.72rem', color: 'var(--fg-muted)' }}>Video</span>
        <div ref={rulerRef} className="ce-track-row" onPointerDown={startScrub}>
          {/* Kept region base */}
          <div
            style={{
              position: 'absolute',
              top: 3,
              bottom: 3,
              left: pct(project.trim.in),
              width: `${((project.trim.out - project.trim.in) / dur) * 100}%`,
              background: 'rgba(228, 255, 69, 0.18)',
              borderRadius: 4,
            }}
          />

          {/* Split sub-segments (click between two split marks to select for cut) */}
          {splitMarks.length > 0 &&
            splitBoundaries.slice(0, -1).map((segA, idx) => {
              const segB = splitBoundaries[idx + 1];
              const isSel =
                selectedCutRange &&
                Math.abs(selectedCutRange[0] - segA) < 0.02 &&
                Math.abs(selectedCutRange[1] - segB) < 0.02;
              return (
                <div
                  key={idx}
                  onClick={(ev) => {
                    ev.stopPropagation();
                    onSelectCutRange([segA, segB]);
                  }}
                  style={{
                    position: 'absolute',
                    top: 3,
                    bottom: 3,
                    left: pct(segA),
                    width: `${((segB - segA) / dur) * 100}%`,
                    borderLeft: idx > 0 ? '2px dashed var(--accent)' : undefined,
                    background: isSel ? 'rgba(255, 107, 92, 0.28)' : 'transparent',
                    cursor: 'pointer',
                  }}
                />
              );
            })}

          {/* Hatched cuts */}
          {project.trim.cuts.map(([a, b], idx) => (
            <div
              key={idx}
              className="ce-cut-hatch"
              title={`Bagian dipotong ${a.toFixed(1)}s - ${b.toFixed(1)}s`}
              style={{
                left: pct(a),
                width: `${((b - a) / dur) * 100}%`,
              }}
            />
          ))}

          {/* Trim In / Out handles */}
          <div
            onPointerDown={(e) => startTrimHandle(e, 'in')}
            title="Geser titik mulai (Trim In)"
            style={{
              position: 'absolute',
              top: 0,
              bottom: 0,
              left: pct(project.trim.in),
              width: 8,
              transform: 'translateX(-50%)',
              background: 'var(--accent)',
              cursor: 'ew-resize',
            }}
          />
          <div
            onPointerDown={(e) => startTrimHandle(e, 'out')}
            title="Geser titik selesai (Trim Out)"
            style={{
              position: 'absolute',
              top: 0,
              bottom: 0,
              left: pct(project.trim.out),
              width: 8,
              transform: 'translateX(-50%)',
              background: 'var(--accent)',
              cursor: 'ew-resize',
            }}
          />

          {/* Playhead */}
          <div
            style={{
              position: 'absolute',
              top: 0,
              bottom: 0,
              left: pct(currentTime),
              width: 2,
              background: '#fff',
              pointerEvents: 'none',
            }}
          />
        </div>

        {/* Subtitle track */}
        <span style={{ fontSize: '0.72rem', color: 'var(--fg-muted)' }}>Subtitle</span>
        <div className="ce-track-row" style={{ height: 26 }} onPointerDown={startScrub}>
          {project.subtitles.enabled &&
            project.subtitles.lines.map((line) => {
              if (line.words.length === 0) return null;
              const s = line.words[0].s;
              const e = line.words[line.words.length - 1].e;
              return (
                <div
                  key={line.id}
                  onClick={(ev) => {
                    ev.stopPropagation();
                    onSeek(s);
                  }}
                  title={line.words.map((w) => w.w).join(' ')}
                  style={{
                    position: 'absolute',
                    top: 4,
                    bottom: 4,
                    left: pct(s),
                    width: `${Math.max(0.8, ((e - s) / dur) * 100)}%`,
                    background: 'rgba(127, 255, 160, 0.25)',
                    border: '1px solid rgba(127, 255, 160, 0.55)',
                    borderRadius: 3,
                    cursor: 'pointer',
                  }}
                />
              );
            })}
        </div>

        {/* Title track */}
        <span style={{ fontSize: '0.72rem', color: 'var(--fg-muted)' }}>Teks</span>
        <div className="ce-track-row" style={{ height: 28 }} onPointerDown={startScrub}>
          {titleLayers.map((layer) => {
            const end = layer.end ?? project.trim.out;
            const sel = selectedLayerId === layer.id;
            return (
              <div
                key={layer.id}
                onClick={(ev) => {
                  ev.stopPropagation();
                  onSelectLayer(layer.id);
                }}
                style={{
                  position: 'absolute',
                  top: 3,
                  bottom: 3,
                  left: pct(layer.start),
                  width: `${Math.max(1.5, ((end - layer.start) / dur) * 100)}%`,
                  background: sel ? 'rgba(228, 255, 69, 0.35)' : 'rgba(228, 255, 69, 0.2)',
                  border: `1px solid ${sel ? 'var(--accent)' : 'rgba(228,255,69,0.5)'}`,
                  borderRadius: 4,
                  fontSize: '0.68rem',
                  padding: '0 8px',
                  overflow: 'hidden',
                  whiteSpace: 'nowrap',
                  cursor: 'pointer',
                }}
              >
                <span
                  onPointerDown={(e) => startLayerEdgeDrag(e, layer, 'start')}
                  style={{
                    position: 'absolute',
                    left: 0,
                    top: 0,
                    bottom: 0,
                    width: 6,
                    cursor: 'ew-resize',
                    background: 'var(--accent)',
                  }}
                />
                {layer.text}
                <span
                  onPointerDown={(e) => startLayerEdgeDrag(e, layer, 'end')}
                  style={{
                    position: 'absolute',
                    right: 0,
                    top: 0,
                    bottom: 0,
                    width: 6,
                    cursor: 'ew-resize',
                    background: 'var(--accent)',
                  }}
                />
              </div>
            );
          })}
        </div>

        {/* Overlay track */}
        <span style={{ fontSize: '0.72rem', color: 'var(--fg-muted)' }}>Overlay</span>
        <div className="ce-track-row" style={{ height: 28 }} onPointerDown={startScrub}>
          {overlayLayers.map((layer) => {
            const end = layer.end ?? project.trim.out;
            const sel = selectedLayerId === layer.id;
            return (
              <div
                key={layer.id}
                onClick={(ev) => {
                  ev.stopPropagation();
                  onSelectLayer(layer.id);
                }}
                style={{
                  position: 'absolute',
                  top: 3,
                  bottom: 3,
                  left: pct(layer.start),
                  width: `${Math.max(1.5, ((end - layer.start) / dur) * 100)}%`,
                  background: sel ? 'rgba(99, 179, 255, 0.35)' : 'rgba(99, 179, 255, 0.2)',
                  border: '1px solid rgba(99, 179, 255, 0.65)',
                  borderRadius: 4,
                  fontSize: '0.68rem',
                  padding: '0 8px',
                  overflow: 'hidden',
                  whiteSpace: 'nowrap',
                  cursor: 'pointer',
                }}
              >
                <span
                  onPointerDown={(e) => startLayerEdgeDrag(e, layer, 'start')}
                  style={{
                    position: 'absolute',
                    left: 0,
                    top: 0,
                    bottom: 0,
                    width: 6,
                    cursor: 'ew-resize',
                    background: '#63b3ff',
                  }}
                />
                {layer.src.replace('assets/', '')}
                <span
                  onPointerDown={(e) => startLayerEdgeDrag(e, layer, 'end')}
                  style={{
                    position: 'absolute',
                    right: 0,
                    top: 0,
                    bottom: 0,
                    width: 6,
                    cursor: 'ew-resize',
                    background: '#63b3ff',
                  }}
                />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
