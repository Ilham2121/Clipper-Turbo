import type React from 'react';
import { computeTitleHash, renderTitlePng } from './titleTemplates';
import type { AspectFormat, ClipAsset, ClipProject, EditorLayer } from './types';
import type { ProjectAction } from './useProject';

/**
 * Render & upload transparent PNGs for any title layer whose text/template/scale/rotation
 * changed since its last upload, returning the updated ClipProject ready to save.
 */
export async function syncDirtyTitlePngs(
  clipId: string,
  project: ClipProject,
  format: AspectFormat,
  dispatch: React.Dispatch<ProjectAction>,
): Promise<ClipProject> {
  const updatedLayers: EditorLayer[] = [];
  let changed = false;

  for (const layer of project.layers) {
    if (layer.type !== 'title') {
      updatedLayers.push(layer);
      continue;
    }
    const hash = computeTitleHash(layer, format);
    if (layer.png && layer.pngHash === hash) {
      updatedLayers.push(layer);
      continue;
    }
    const blob = await renderTitlePng(layer, format);
    const form = new FormData();
    form.append('file', blob, `__title_${layer.id}_${hash}.png`);
    const r = await fetch(`/api/clips/${encodeURIComponent(clipId)}/assets`, {
      method: 'POST',
      body: form,
    });
    if (!r.ok) {
      throw new Error('Gagal mengunggah aset PNG judul.');
    }
    const uploaded = (await r.json()) as ClipAsset;
    const patched = { ...layer, png: uploaded.path, pngHash: hash };
    dispatch({
      type: 'UPDATE_LAYER',
      id: layer.id,
      patch: { png: uploaded.path, pngHash: hash },
    });
    updatedLayers.push(patched);
    changed = true;
  }

  return changed ? { ...project, layers: updatedLayers } : project;
}
