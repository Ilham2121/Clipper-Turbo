import React, { useState } from 'react';
import { Film, Image as ImageIcon, Upload } from 'lucide-react';
import { useToast } from '../../components/Toast';
import type { ClipAsset, ClipProject, EditorLayer } from '../types';
import type { ProjectAction } from '../useProject';

interface OverlayToolProps {
  clipId: string;
  project: ClipProject;
  assets: ClipAsset[];
  currentTime: number;
  onAssetUploaded: (asset: ClipAsset) => void;
  onSelectLayer: (id: string) => void;
  dispatch: React.Dispatch<ProjectAction>;
}

export const OverlayTool: React.FC<OverlayToolProps> = ({
  clipId,
  project,
  assets,
  currentTime,
  onAssetUploaded,
  onSelectLayer,
  dispatch,
}) => {
  const [uploading, setUploading] = useState(false);
  const toast = useToast();

  const addAssetToStage = (asset: ClipAsset) => {
    const id = `ov_${Date.now().toString(36)}`;
    const start = Math.max(project.trim.in, Math.min(project.trim.out - 0.5, currentTime));
    const layer: EditorLayer =
      asset.kind === 'video'
        ? {
            id,
            type: 'video',
            src: asset.path,
            x: 0.5,
            y: 0.5,
            scale: 0.45,
            opacity: 1.0,
            muted: true,
            start,
            end: Math.min(project.trim.out, start + 5.0),
          }
        : {
            id,
            type: 'image',
            src: asset.path,
            x: 0.82,
            y: 0.12,
            scale: 0.22,
            opacity: 1.0,
            start: project.trim.in,
            end: null,
          };
    dispatch({ type: 'ADD_LAYER', layer });
    onSelectLayer(id);
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (file.size > 50 * 1024 * 1024) {
      toast.error('File Terlalu Besar', 'Maksimal ukuran overlay adalah 50 MB.');
      return;
    }
    setUploading(true);
    try {
      const form = new FormData();
      form.append('file', file);
      const r = await fetch(
        `/api/clips/${encodeURIComponent(clipId)}/assets`,
        { method: 'POST', body: form },
      );
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: 'Upload gagal' }));
        throw new Error(String(err.detail || 'Upload gagal'));
      }
      const asset = (await r.json()) as ClipAsset;
      onAssetUploaded(asset);
      addAssetToStage(asset);
      toast.success('Aset Ditambahkan', asset.name);
    } catch (err) {
      toast.error(
        'Gagal Mengunggah Overlay',
        err instanceof Error ? err.message : 'Format tidak didukung.',
      );
    } finally {
      setUploading(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      <div>
        <h4 style={{ fontSize: '0.88rem', marginBottom: '0.2rem' }}>
          Overlay Gambar & B-Roll
        </h4>
        <p style={{ fontSize: '0.74rem', color: 'var(--fg-muted)' }}>
          PNG, JPG, WEBP, MP4, MOV, atau WEBM (maks. 50 MB).
        </p>
      </div>

      <label
        className="btn-secondary"
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '0.45rem',
          cursor: uploading ? 'wait' : 'pointer',
        }}
      >
        <Upload size={14} />
        <span>{uploading ? 'Mengunggah...' : 'Unggah Gambar / Video'}</span>
        <input
          type="file"
          accept=".png,.jpg,.jpeg,.webp,.mp4,.mov,.webm"
          disabled={uploading}
          onChange={handleFileChange}
          style={{ display: 'none' }}
        />
      </label>

      <div style={{ display: 'grid', gap: '0.45rem' }}>
        {assets.map((a) => (
          <button
            key={a.path}
            type="button"
            onClick={() => addAssetToStage(a)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
              padding: '0.5rem',
              background: 'var(--surface-2)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--fg)',
              cursor: 'pointer',
              textAlign: 'left',
            }}
          >
            {a.kind === 'video' ? (
              <Film size={15} color="var(--accent)" />
            ) : (
              <ImageIcon size={15} color="var(--accent)" />
            )}
            <span
              style={{
                fontSize: '0.76rem',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {a.name}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
};
