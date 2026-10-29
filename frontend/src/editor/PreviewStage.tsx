import React, { useEffect, useRef, useState } from 'react';
import { Pause, Play, Shield } from 'lucide-react';
import { layoutTitle } from './titleTemplates';
import { nextKeptTime, outputDuration, toOutput } from './timeMap';
import type {
  AspectFormat,
  ClipProject,
  EditorLayer,
  SubtitlePresetId,
  TitleLayer,
} from './types';
import type { ProjectAction } from './useProject';

interface PreviewStageProps {
  clipId: string;
  masterUrl: string;
  format: AspectFormat;
  project: ClipProject;
  currentTime: number;
  playing: boolean;
  selectedLayerId: string | null;
  onTimeChange: (t: number) => void;
  onPlayingChange: (playing: boolean) => void;
  onSelectLayer: (id: string | null) => void;
  dispatch: React.Dispatch<ProjectAction>;
}

const SUBTITLE_PRESET_STYLE: Record<
  SubtitlePresetId,
  {
    fontFamily: string;
    fontSize: number;
    textColor: string;
    highlightColor: string;
    strokeWidth: number;
    uppercase: boolean;
    singleWord: boolean;
  }
> = {
  tiktok_viral: {
    fontFamily: 'Montserrat, sans-serif',
    fontSize: 72,
    textColor: '#FFFFFF',
    highlightColor: '#FFD700',
    strokeWidth: 10,
    uppercase: true,
    singleWord: false,
  },
  classic_karaoke: {
    fontFamily: 'Inter, sans-serif',
    fontSize: 68,
    textColor: '#FFFFFF',
    highlightColor: '#00FF88',
    strokeWidth: 8,
    uppercase: false,
    singleWord: false,
  },
  minimalist: {
    fontFamily: 'Inter, sans-serif',
    fontSize: 60,
    textColor: '#FFFFFF',
    highlightColor: '#FFFFFF',
    strokeWidth: 0,
    uppercase: false,
    singleWord: false,
  },
  word_pop: {
    fontFamily: 'Montserrat, sans-serif',
    fontSize: 96,
    textColor: '#FFFFFF',
    highlightColor: '#00D7FF',
    strokeWidth: 12,
    uppercase: true,
    singleWord: true,
  },
};

function fmtClock(sec: number): string {
  const s = Math.max(0, sec);
  const m = Math.floor(s / 60);
  const rem = s - m * 60;
  return `${String(m).padStart(2, '0')}:${rem.toFixed(1).padStart(4, '0')}`;
}

export const PreviewStage: React.FC<PreviewStageProps> = ({
  clipId,
  masterUrl,
  format,
  project,
  currentTime,
  playing,
  selectedLayerId,
  onTimeChange,
  onPlayingChange,
  onSelectLayer,
  dispatch,
}) => {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [stageScale, setStageScale] = useState(0.24);
  const [safeZone, setSafeZone] = useState(false);
  const [editingTitleId, setEditingTitleId] = useState<string | null>(null);

  const frameW = format === '16:9' ? 1920 : 1080;
  const frameH = format === '16:9' ? 1080 : 1920;

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const compute = () => {
      const availW = Math.max(200, el.clientWidth - 24);
      const availH = Math.max(220, el.clientHeight - 54);
      setStageScale(Math.min(availW / frameW, availH / frameH));
    };
    compute();
    const ro = new ResizeObserver(compute);
    ro.observe(el);
    return () => ro.disconnect();
  }, [frameW, frameH]);

  // Keep <video> synced with external seek changes
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    if (Math.abs(v.currentTime - currentTime) > 0.08) {
      v.currentTime = currentTime;
    }
  }, [currentTime]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    if (playing) {
      const valid = nextKeptTime(v.currentTime, project.trim);
      if (valid === null) {
        v.currentTime = project.trim.in;
      } else if (Math.abs(valid - v.currentTime) > 0.02) {
        v.currentTime = valid;
      }
      void v.play().catch(() => onPlayingChange(false));
    } else {
      v.pause();
    }
  }, [playing, project.trim, onPlayingChange]);

  const handleTimeUpdate = () => {
    const v = videoRef.current;
    if (!v) return;
    const t = v.currentTime;
    const next = nextKeptTime(t, project.trim);
    if (next === null) {
      v.pause();
      v.currentTime = project.trim.out;
      onTimeChange(project.trim.out);
      onPlayingChange(false);
      return;
    }
    if (Math.abs(next - t) > 0.02) {
      v.currentTime = next;
      onTimeChange(next);
      return;
    }
    onTimeChange(t);
  };

  const startDragLayer = (e: React.PointerEvent, layer: EditorLayer) => {
    if (editingTitleId === layer.id) return;
    e.stopPropagation();
    onSelectLayer(layer.id);
    const startX = e.clientX;
    const startY = e.clientY;
    const origX = layer.x;
    const origY = layer.y;

    const onMove = (ev: PointerEvent) => {
      const dx = (ev.clientX - startX) / (frameW * stageScale);
      const dy = (ev.clientY - startY) / (frameH * stageScale);
      dispatch({
        type: 'UPDATE_LAYER',
        id: layer.id,
        patch: {
          x: Math.max(0.05, Math.min(0.95, origX + dx)),
          y: Math.max(0.05, Math.min(0.95, origY + dy)),
        },
      });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  const startResizeLayer = (e: React.PointerEvent, layer: EditorLayer) => {
    e.stopPropagation();
    const startX = e.clientX;
    const origScale = layer.scale;
    const onMove = (ev: PointerEvent) => {
      const delta = (ev.clientX - startX) / (320 * stageScale);
      const nextScale = Math.max(0.1, Math.min(3.0, origScale + delta));
      dispatch({
        type: 'UPDATE_LAYER',
        id: layer.id,
        patch:
          layer.type === 'title'
            ? { scale: nextScale, pngHash: null }
            : { scale: nextScale },
      });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  // Active subtitle line at currentTime
  const activeLine = project.subtitles.enabled
    ? project.subtitles.lines.find((line) => {
        if (line.words.length === 0) return false;
        const s = line.words[0].s;
        const e = line.words[line.words.length - 1].e;
        return currentTime >= s - 0.04 && currentTime <= e + 0.08;
      })
    : undefined;

  const subPreset = SUBTITLE_PRESET_STYLE[project.subtitles.preset];

  const renderTitleDom = (layer: TitleLayer) => {
    const layout = layoutTitle(layer.text, layer.template, frameW);
    const { spec } = layout;
    if (editingTitleId === layer.id) {
      return (
        <textarea
          autoFocus
          defaultValue={layer.text}
          onBlur={(ev) => {
            dispatch({
              type: 'UPDATE_LAYER',
              id: layer.id,
              patch: { text: ev.target.value, pngHash: null },
            });
            setEditingTitleId(null);
          }}
          onKeyDown={(ev) => {
            if (ev.key === 'Escape') setEditingTitleId(null);
          }}
          style={{
            width: layout.boxWidth,
            minHeight: layout.boxHeight,
            fontSize: spec.fontSize,
            fontFamily: spec.fontFamily,
            fontWeight: spec.fontWeight,
            textAlign: 'center',
            background: '#111',
            color: '#fff',
            border: '3px solid var(--accent)',
            borderRadius: 18,
            padding: 16,
          }}
        />
      );
    }

    return (
      <div
        onDoubleClick={(ev) => {
          ev.stopPropagation();
          setEditingTitleId(layer.id);
        }}
        style={{
          position: 'relative',
          padding: `${spec.paddingY}px ${spec.paddingX}px`,
          background: spec.boxBg ?? 'transparent',
          borderRadius: spec.boxRadius,
          boxShadow: spec.boxBg ? '0 8px 24px rgba(0,0,0,0.35)' : undefined,
          textAlign: 'center',
          whiteSpace: 'nowrap',
        }}
      >
        {layout.lines.map((line, li) => (
          <div
            key={li}
            style={{
              fontFamily: `"${spec.fontFamily}", sans-serif`,
              fontWeight: spec.fontWeight,
              fontSize: line.fontSize,
              lineHeight: `${line.lineHeightPx}px`,
            }}
          >
            {line.tokens.map((tk, ti) => {
              const gradStyle: React.CSSProperties =
                spec.gradientStops && !tk.highlight
                  ? {
                      backgroundImage: `linear-gradient(180deg, ${spec.gradientStops[0]}, ${spec.gradientStops[1]})`,
                      WebkitBackgroundClip: 'text',
                      WebkitTextFillColor: 'transparent',
                    }
                  : {};
              return (
                <React.Fragment key={ti}>
                  {ti > 0 && ' '}
                  <span
                    style={{
                      display: 'inline-block',
                      padding: tk.highlight ? '0 12px' : undefined,
                      borderRadius: tk.highlight ? 12 : undefined,
                      background: tk.highlight ? spec.highlightBg : undefined,
                      color: tk.highlight ? spec.highlightColor : spec.textColor,
                      WebkitTextStroke:
                        spec.strokeColor && !tk.highlight
                          ? `${spec.strokeWidth}px ${spec.strokeColor}`
                          : undefined,
                      paintOrder: 'stroke fill',
                      ...gradStyle,
                    }}
                  >
                    {tk.text}
                  </span>
                </React.Fragment>
              );
            })}
          </div>
        ))}
      </div>
    );
  };

  const outNow = toOutput(currentTime, project.trim) ?? 0;
  const outTotal = outputDuration(project.trim);

  return (
    <div ref={containerRef} className="ce-center">
      <div
        className="ce-stage-viewport"
        style={{
          width: Math.round(frameW * stageScale),
          height: Math.round(frameH * stageScale),
        }}
        onPointerDown={() => onSelectLayer(null)}
      >
        <div
          style={{
            width: frameW,
            height: frameH,
            transform: `scale(${stageScale})`,
            transformOrigin: 'top left',
            position: 'relative',
          }}
        >
          <video
            ref={videoRef}
            src={masterUrl}
            onTimeUpdate={handleTimeUpdate}
            onEnded={() => onPlayingChange(false)}
            playsInline
            style={{
              width: frameW,
              height: frameH,
              objectFit: 'contain',
              display: 'block',
              background: '#000',
            }}
          />

          {/* Live Karaoke Subtitles */}
          {activeLine && (
            <div
              style={{
                position: 'absolute',
                left: '7%',
                right: '7%',
                top: `${project.subtitles.position_y * 100}%`,
                transform: 'translateY(-50%)',
                textAlign: 'center',
                pointerEvents: 'none',
                fontFamily: subPreset.fontFamily,
                fontWeight: 800,
                fontSize: subPreset.fontSize,
                lineHeight: 1.2,
              }}
            >
              {(() => {
                const activeWord =
                  activeLine.words.find(
                    (w) => currentTime >= w.s && currentTime <= w.e,
                  ) ?? activeLine.words[0];
                const wordsToShow = subPreset.singleWord
                  ? [activeWord]
                  : activeLine.words;
                return wordsToShow.map((w, idx) => {
                  const isAct = w === activeWord;
                  const txt = subPreset.uppercase ? w.w.toUpperCase() : w.w;
                  return (
                    <span
                      key={idx}
                      style={{
                        color: isAct
                          ? subPreset.highlightColor
                          : subPreset.textColor,
                        WebkitTextStroke:
                          subPreset.strokeWidth > 0
                            ? `${subPreset.strokeWidth}px #000`
                            : undefined,
                        paintOrder: 'stroke fill',
                        marginRight: '0.28em',
                      }}
                    >
                      {txt}
                    </span>
                  );
                });
              })()}
            </div>
          )}

          {/* Active Title & Image/Video Overlay Layers */}
          {project.layers.map((layer) => {
            const lEnd = layer.end ?? project.trim.out;
            if (currentTime < layer.start - 0.02 || currentTime > lEnd + 0.02) {
              return null;
            }
            const isSelected = selectedLayerId === layer.id;
            const assetUrl =
              layer.type !== 'title'
                ? `/api/clips/${encodeURIComponent(clipId)}/${layer.src}`
                : '';

            return (
              <div
                key={layer.id}
                className={`ce-layer-box ${isSelected ? 'selected' : ''}`}
                onPointerDown={(e) => startDragLayer(e, layer)}
                style={{
                  left: `${layer.x * 100}%`,
                  top: `${layer.y * 100}%`,
                  transform:
                    layer.type === 'title'
                      ? `translate(-50%, -50%) scale(${layer.scale}) rotate(${layer.rotation}deg)`
                      : 'translate(-50%, -50%)',
                }}
              >
                {layer.type === 'title' && renderTitleDom(layer)}
                {layer.type === 'image' && (
                  <img
                    src={assetUrl}
                    alt="overlay"
                    draggable={false}
                    style={{
                      width: Math.round(layer.scale * frameW),
                      opacity: layer.opacity,
                      display: 'block',
                    }}
                  />
                )}
                {layer.type === 'video' && (
                  <video
                    src={assetUrl}
                    muted
                    autoPlay={playing}
                    loop
                    playsInline
                    style={{
                      width: Math.round(layer.scale * frameW),
                      opacity: layer.opacity,
                      display: 'block',
                    }}
                  />
                )}
                {isSelected && (
                  <div
                    className="ce-resize-handle"
                    onPointerDown={(e) => startResizeLayer(e, layer)}
                  />
                )}
              </div>
            );
          })}

          {/* TikTok / Shorts UI Safe Zone Guide */}
          {safeZone && format === '9:16' && (
            <div
              style={{
                position: 'absolute',
                inset: 0,
                pointerEvents: 'none',
                borderTop: '150px solid rgba(228, 255, 69, 0.12)',
                borderBottom: '360px solid rgba(228, 255, 69, 0.12)',
                borderRight: '140px solid rgba(228, 255, 69, 0.12)',
                outline: '2px dashed rgba(228, 255, 69, 0.55)',
                outlineOffset: -4,
              }}
            />
          )}
        </div>
      </div>

      {/* Transport bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.75rem',
          fontSize: '0.82rem',
        }}
      >
        <button
          type="button"
          className="btn-secondary"
          style={{ padding: '0.35rem 0.7rem' }}
          onClick={() => onPlayingChange(!playing)}
        >
          {playing ? <Pause size={14} /> : <Play size={14} />}
        </button>
        <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--fg-muted)' }}>
          {fmtClock(outNow)} / {fmtClock(outTotal)}
        </span>
        {format === '9:16' && (
          <button
            type="button"
            className="btn-secondary"
            style={{
              padding: '0.3rem 0.6rem',
              fontSize: '0.75rem',
              borderColor: safeZone ? 'var(--accent)' : undefined,
              color: safeZone ? 'var(--accent)' : undefined,
            }}
            onClick={() => setSafeZone((s) => !s)}
          >
            <Shield size={12} /> Safe Zone
          </button>
        )}
      </div>
    </div>
  );
};
