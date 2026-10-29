import React from 'react';
import { RotateCcw } from 'lucide-react';
import { outputDuration } from '../timeMap';
import type { ClipProject } from '../types';
import type { ProjectAction } from '../useProject';

interface TrimToolProps {
  project: ClipProject;
  masterDuration: number;
  dispatch: React.Dispatch<ProjectAction>;
}

export const TrimTool: React.FC<TrimToolProps> = ({
  project,
  masterDuration,
  dispatch,
}) => {
  const netDur = outputDuration(project.trim);
  const ideal = netDur <= 60;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
      <div
        style={{
          padding: '0.65rem',
          background: 'var(--surface-2)',
          borderRadius: 'var(--radius-sm)',
          border: '1px solid var(--border)',
        }}
      >
        <div style={{ fontSize: '0.72rem', color: 'var(--fg-muted)' }}>
          Durasi Akhir Klip
        </div>
        <div
          style={{
            display: 'flex',
            alignItems: 'baseline',
            justifyContent: 'space-between',
            marginTop: '0.2rem',
          }}
        >
          <strong style={{ fontSize: '1.2rem', fontFamily: 'var(--font-mono)' }}>
            {netDur.toFixed(2)}s
          </strong>
          <span
            style={{
              fontSize: '0.7rem',
              color: ideal ? 'var(--success)' : 'var(--warning)',
            }}
          >
            {ideal ? 'Ideal Shorts/TikTok (≤60s)' : 'Lebih dari 60s'}
          </span>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
        <div>
          <label className="form-label" style={{ fontSize: '0.72rem' }}>
            Titik Mulai (s)
          </label>
          <input
            type="number"
            step={0.1}
            min={0}
            max={Math.max(0, project.trim.out - 1)}
            className="input-text"
            value={project.trim.in}
            onChange={(e) => {
              const v = Math.max(
                0,
                Math.min(project.trim.out - 1, Number(e.target.value) || 0),
              );
              dispatch({
                type: 'SET_TRIM',
                trim: { ...project.trim, in: v },
              });
            }}
          />
        </div>
        <div>
          <label className="form-label" style={{ fontSize: '0.72rem' }}>
            Titik Selesai (s)
          </label>
          <input
            type="number"
            step={0.1}
            min={project.trim.in + 1}
            max={masterDuration}
            className="input-text"
            value={project.trim.out}
            onChange={(e) => {
              const v = Math.min(
                masterDuration,
                Math.max(
                  project.trim.in + 1,
                  Number(e.target.value) || project.trim.out,
                ),
              );
              dispatch({
                type: 'SET_TRIM',
                trim: { ...project.trim, out: v },
              });
            }}
          />
        </div>
      </div>

      <p style={{ fontSize: '0.73rem', color: 'var(--fg-muted)' }}>
        Untuk membuang jeda di tengah klip: posisikan playhead lalu tekan{' '}
        <strong>S</strong> di awal dan akhir jeda, klik segmen di antaranya pada
        track Video, lalu tekan <strong>Delete</strong>.
      </p>

      {project.trim.cuts.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
          <span style={{ fontSize: '0.75rem', fontWeight: 600 }}>
            Bagian Dipotong ({project.trim.cuts.length})
          </span>
          {project.trim.cuts.map(([a, b], idx) => (
            <div
              key={idx}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '0.4rem 0.55rem',
                background: 'var(--surface-2)',
                borderRadius: 'var(--radius-sm)',
                fontSize: '0.76rem',
              }}
            >
              <span style={{ fontFamily: 'var(--font-mono)' }}>
                {a.toFixed(2)}s – {b.toFixed(2)}s
              </span>
              <button
                type="button"
                className="btn-secondary"
                style={{ padding: '0.2rem 0.45rem', fontSize: '0.7rem' }}
                onClick={() => dispatch({ type: 'REMOVE_CUT', index: idx })}
              >
                <RotateCcw size={11} /> Pulihkan
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
