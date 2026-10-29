import React from 'react';
import { ArrowDown, ArrowUp, Trash2 } from 'lucide-react';
import { TITLE_TEMPLATES } from './titleTemplates';
import type { ClipProject, TitleTemplateId } from './types';
import type { ProjectAction } from './useProject';

interface PropertiesPanelProps {
  project: ClipProject;
  masterDuration: number;
  selectedLayerId: string | null;
  onSelectLayer: (id: string | null) => void;
  dispatch: React.Dispatch<ProjectAction>;
}

export const PropertiesPanel: React.FC<PropertiesPanelProps> = ({
  project,
  masterDuration,
  selectedLayerId,
  onSelectLayer,
  dispatch,
}) => {
  const layer = project.layers.find((l) => l.id === selectedLayerId);

  if (!layer) {
    return (
      <div className="ce-right">
        <h4 style={{ fontSize: '0.86rem' }}>Properti Layer</h4>
        <p style={{ fontSize: '0.76rem', color: 'var(--fg-muted)' }}>
          Pilih judul atau overlay di preview atau timeline untuk mengatur gaya,
          ukuran, dan durasi tampilnya.
        </p>
      </div>
    );
  }

  const endVal = layer.end ?? project.trim.out;

  return (
    <div className="ce-right">
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <h4 style={{ fontSize: '0.86rem' }}>
          {layer.type === 'title'
            ? 'Properti Judul'
            : layer.type === 'video'
              ? 'Overlay Video'
              : 'Overlay Gambar'}
        </h4>
        <div style={{ display: 'flex', gap: '0.25rem' }}>
          <button
            type="button"
            className="btn-secondary"
            style={{ padding: '0.2rem 0.4rem' }}
            title="Naikkan urutan layer"
            onClick={() =>
              dispatch({ type: 'REORDER_LAYER', id: layer.id, direction: 'up' })
            }
          >
            <ArrowUp size={12} />
          </button>
          <button
            type="button"
            className="btn-secondary"
            style={{ padding: '0.2rem 0.4rem' }}
            title="Turunkan urutan layer"
            onClick={() =>
              dispatch({
                type: 'REORDER_LAYER',
                id: layer.id,
                direction: 'down',
              })
            }
          >
            <ArrowDown size={12} />
          </button>
          <button
            type="button"
            className="btn-secondary"
            style={{ padding: '0.2rem 0.4rem', color: 'var(--danger)' }}
            title="Hapus layer"
            onClick={() => {
              dispatch({ type: 'REMOVE_LAYER', id: layer.id });
              onSelectLayer(null);
            }}
          >
            <Trash2 size={12} />
          </button>
        </div>
      </div>

      {layer.type === 'title' && (
        <>
          <div>
            <label className="form-label" style={{ fontSize: '0.73rem' }}>
              Teks Judul (*kata* untuk highlight)
            </label>
            <textarea
              className="input-text"
              rows={3}
              value={layer.text}
              onChange={(e) =>
                dispatch({
                  type: 'UPDATE_LAYER',
                  id: layer.id,
                  patch: { text: e.target.value, pngHash: null },
                })
              }
            />
          </div>

          <div>
            <label className="form-label" style={{ fontSize: '0.73rem' }}>
              Gaya Template
            </label>
            <select
              className="select-input"
              value={layer.template}
              onChange={(e) =>
                dispatch({
                  type: 'UPDATE_LAYER',
                  id: layer.id,
                  patch: {
                    template: e.target.value as TitleTemplateId,
                    pngHash: null,
                  },
                })
              }
            >
              {TITLE_TEMPLATES.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="form-label" style={{ fontSize: '0.73rem' }}>
              Rotasi ({layer.rotation}°)
            </label>
            <input
              type="range"
              min={-25}
              max={25}
              step={1}
              value={layer.rotation}
              onChange={(e) =>
                dispatch({
                  type: 'UPDATE_LAYER',
                  id: layer.id,
                  patch: { rotation: Number(e.target.value), pngHash: null },
                })
              }
              style={{ width: '100%' }}
            />
          </div>
        </>
      )}

      <div>
        <label className="form-label" style={{ fontSize: '0.73rem' }}>
          Ukuran / Skala ({Math.round(layer.scale * 100)}%)
        </label>
        <input
          type="range"
          min={0.1}
          max={2.2}
          step={0.02}
          value={layer.scale}
          onChange={(e) =>
            dispatch({
              type: 'UPDATE_LAYER',
              id: layer.id,
              patch:
                layer.type === 'title'
                  ? { scale: Number(e.target.value), pngHash: null }
                  : { scale: Number(e.target.value) },
            })
          }
          style={{ width: '100%' }}
        />
      </div>

      {layer.type !== 'title' && (
        <div>
          <label className="form-label" style={{ fontSize: '0.73rem' }}>
            Transparansi / Opacity ({Math.round(layer.opacity * 100)}%)
          </label>
          <input
            type="range"
            min={0.1}
            max={1}
            step={0.05}
            value={layer.opacity}
            onChange={(e) =>
              dispatch({
                type: 'UPDATE_LAYER',
                id: layer.id,
                patch: { opacity: Number(e.target.value) },
              })
            }
            style={{ width: '100%' }}
          />
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
        <div>
          <label className="form-label" style={{ fontSize: '0.72rem' }}>
            Mulai (s)
          </label>
          <input
            type="number"
            step={0.1}
            min={0}
            max={Math.max(0, endVal - 0.2)}
            className="input-text"
            value={layer.start}
            onChange={(e) =>
              dispatch({
                type: 'UPDATE_LAYER',
                id: layer.id,
                patch: { start: Number(e.target.value) || 0 },
              })
            }
          />
        </div>
        <div>
          <label className="form-label" style={{ fontSize: '0.72rem' }}>
            Selesai (s)
          </label>
          <input
            type="number"
            step={0.1}
            min={layer.start + 0.2}
            max={masterDuration}
            className="input-text"
            value={endVal}
            onChange={(e) =>
              dispatch({
                type: 'UPDATE_LAYER',
                id: layer.id,
                patch: { end: Number(e.target.value) || endVal },
              })
            }
          />
        </div>
      </div>

      <button
        type="button"
        className="btn-secondary"
        style={{ fontSize: '0.75rem' }}
        onClick={() =>
          dispatch({
            type: 'UPDATE_LAYER',
            id: layer.id,
            patch: { start: project.trim.in, end: null },
          })
        }
      >
        Tampilkan Sepanjang Klip
      </button>
    </div>
  );
};
