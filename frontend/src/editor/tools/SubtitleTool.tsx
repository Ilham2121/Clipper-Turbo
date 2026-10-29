import React from 'react';
import { Merge, Scissors } from 'lucide-react';
import {
  applyLineTextEdit,
  mergeSubtitleLines,
  splitSubtitleLine,
} from '../wordDiff';
import type { ClipProject, SubtitlePresetId } from '../types';
import type { ProjectAction } from '../useProject';

interface SubtitleToolProps {
  project: ClipProject;
  onSeek: (t: number) => void;
  dispatch: React.Dispatch<ProjectAction>;
}

const PRESET_OPTIONS: { id: SubtitlePresetId; label: string; desc: string }[] = [
  { id: 'tiktok_viral', label: 'TikTok Viral', desc: 'Montserrat bold + kuning' },
  { id: 'classic_karaoke', label: 'Classic Karaoke', desc: 'Inter + hijau neon' },
  { id: 'minimalist', label: 'Minimalist', desc: 'Teks bersih tanpa outline' },
  { id: 'word_pop', label: 'Word Pop', desc: '1 kata besar bergantian' },
];

export const SubtitleTool: React.FC<SubtitleToolProps> = ({
  project,
  onSeek,
  dispatch,
}) => {
  const { subtitles } = project;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
      <label
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          fontSize: '0.82rem',
          fontWeight: 600,
        }}
      >
        <span>Aktifkan Subtitle</span>
        <input
          type="checkbox"
          checked={subtitles.enabled}
          onChange={(e) =>
            dispatch({
              type: 'SET_SUBTITLES_META',
              patch: { enabled: e.target.checked },
            })
          }
        />
      </label>

      <div>
        <label className="form-label" style={{ fontSize: '0.75rem' }}>
          Preset Gaya
        </label>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.4rem' }}>
          {PRESET_OPTIONS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() =>
                dispatch({
                  type: 'SET_SUBTITLES_META',
                  patch: { preset: p.id },
                })
              }
              style={{
                padding: '0.45rem 0.5rem',
                borderRadius: 'var(--radius-sm)',
                border: `1px solid ${
                  subtitles.preset === p.id ? 'var(--accent)' : 'var(--border)'
                }`,
                background:
                  subtitles.preset === p.id
                    ? 'var(--surface-3)'
                    : 'var(--surface-2)',
                color: 'var(--fg)',
                textAlign: 'left',
                cursor: 'pointer',
              }}
            >
              <div style={{ fontSize: '0.76rem', fontWeight: 700 }}>{p.label}</div>
              <div style={{ fontSize: '0.66rem', color: 'var(--fg-muted)' }}>
                {p.desc}
              </div>
            </button>
          ))}
        </div>
      </div>

      <div>
        <label className="form-label" style={{ fontSize: '0.75rem' }}>
          Posisi Vertikal ({Math.round(subtitles.position_y * 100)}%)
        </label>
        <input
          type="range"
          min={0.1}
          max={0.9}
          step={0.01}
          value={subtitles.position_y}
          onChange={(e) =>
            dispatch({
              type: 'SET_SUBTITLES_META',
              patch: { position_y: Number(e.target.value) },
            })
          }
          style={{ width: '100%' }}
        />
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        <span style={{ fontSize: '0.76rem', color: 'var(--fg-muted)' }}>
          Baris Subtitle ({subtitles.lines.length})
        </span>
        {subtitles.lines.map((line, idx) => {
          const lineStart = line.words[0]?.s ?? project.trim.in;
          const lineEnd =
            line.words[line.words.length - 1]?.e ?? lineStart + 1.0;
          const textValue = line.words.map((w) => w.w).join(' ');
          return (
            <div
              key={line.id}
              style={{
                padding: '0.5rem',
                background: 'var(--surface-2)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)',
                display: 'flex',
                flexDirection: 'column',
                gap: '0.35rem',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  fontSize: '0.7rem',
                  color: 'var(--fg-muted)',
                }}
              >
                <button
                  type="button"
                  onClick={() => onSeek(lineStart)}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: 'var(--accent)',
                    fontFamily: 'var(--font-mono)',
                    cursor: 'pointer',
                    fontSize: '0.7rem',
                  }}
                >
                  {lineStart.toFixed(2)}s – {lineEnd.toFixed(2)}s
                </button>
                <div style={{ display: 'flex', gap: '0.25rem' }}>
                  {line.words.length >= 2 && (
                    <button
                      type="button"
                      title="Pisah baris menjadi dua"
                      onClick={() =>
                        dispatch({
                          type: 'SET_SUBTITLE_LINES',
                          lines: splitSubtitleLine(
                            subtitles.lines,
                            idx,
                            Math.ceil(line.words.length / 2),
                          ),
                        })
                      }
                      style={{
                        background: 'var(--surface-3)',
                        border: 'none',
                        color: 'var(--fg)',
                        padding: '2px 5px',
                        borderRadius: 4,
                        cursor: 'pointer',
                      }}
                    >
                      <Scissors size={11} />
                    </button>
                  )}
                  {idx < subtitles.lines.length - 1 && (
                    <button
                      type="button"
                      title="Gabung dengan baris berikutnya"
                      onClick={() =>
                        dispatch({
                          type: 'SET_SUBTITLE_LINES',
                          lines: mergeSubtitleLines(subtitles.lines, idx),
                        })
                      }
                      style={{
                        background: 'var(--surface-3)',
                        border: 'none',
                        color: 'var(--fg)',
                        padding: '2px 5px',
                        borderRadius: 4,
                        cursor: 'pointer',
                      }}
                    >
                      <Merge size={11} />
                    </button>
                  )}
                </div>
              </div>
              <input
                className="input-text"
                style={{ fontSize: '0.8rem', padding: '0.35rem 0.5rem' }}
                value={textValue}
                onChange={(e) => {
                  const updatedWords = applyLineTextEdit(
                    line.words,
                    e.target.value,
                    [lineStart, lineEnd],
                  );
                  const nextLines = subtitles.lines.map((item, i) =>
                    i === idx ? { ...item, words: updatedWords } : item,
                  );
                  dispatch({ type: 'SET_SUBTITLE_LINES', lines: nextLines });
                }}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
};
