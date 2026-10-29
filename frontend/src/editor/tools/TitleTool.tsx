import React from 'react';
import { Plus } from 'lucide-react';
import { TITLE_TEMPLATES } from '../titleTemplates';
import type { ClipProject, TitleLayer, TitleTemplateId } from '../types';
import type { ProjectAction } from '../useProject';

interface TitleToolProps {
  project: ClipProject;
  hookText: string;
  currentTime: number;
  onSelectLayer: (id: string) => void;
  dispatch: React.Dispatch<ProjectAction>;
}

export const TitleTool: React.FC<TitleToolProps> = ({
  project,
  hookText,
  currentTime,
  onSelectLayer,
  dispatch,
}) => {
  const addTitle = (templateId: TitleTemplateId) => {
    const id = `t_${Date.now().toString(36)}`;
    const start = Math.max(
      project.trim.in,
      Math.min(project.trim.out - 0.5, currentTime),
    );
    const end = Math.min(project.trim.out, start + 3.0);
    const layer: TitleLayer = {
      id,
      type: 'title',
      template: templateId,
      text: hookText || 'Tulis *Judul* Viral',
      x: 0.5,
      y: 0.18,
      scale: 1.0,
      rotation: 0,
      start,
      end,
      png: null,
      pngHash: null,
    };
    dispatch({ type: 'ADD_LAYER', layer });
    onSelectLayer(id);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      <div>
        <h4 style={{ fontSize: '0.88rem', marginBottom: '0.2rem' }}>
          Template Judul & Hook
        </h4>
        <p style={{ fontSize: '0.74rem', color: 'var(--fg-muted)' }}>
          Gunakan tanda <code>*kata*</code> untuk memberi highlight warna.
          Klik dua kali teks di preview untuk mengedit langsung.
        </p>
      </div>

      <div style={{ display: 'grid', gap: '0.5rem' }}>
        {TITLE_TEMPLATES.map((tpl) => (
          <button
            key={tpl.id}
            type="button"
            onClick={() => addTitle(tpl.id)}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '0.65rem 0.75rem',
              background: 'var(--surface-2)',
              border: '1px solid var(--border-strong)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--fg)',
              cursor: 'pointer',
              textAlign: 'left',
            }}
          >
            <div>
              <div style={{ fontSize: '0.82rem', fontWeight: 700 }}>
                {tpl.label}
              </div>
              <span style={{ fontSize: '0.7rem', color: 'var(--accent)' }}>
                {tpl.badge}
              </span>
            </div>
            <Plus size={15} color="var(--accent)" />
          </button>
        ))}
      </div>
    </div>
  );
};
