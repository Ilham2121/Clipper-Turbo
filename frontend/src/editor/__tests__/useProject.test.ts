import { describe, expect, it } from 'vitest';
import { createHistoryState, projectReducer } from '../useProject';
import type { ClipProject } from '../types';

function sampleProject(): ClipProject {
  return {
    version: 1,
    trim: { in: 3, out: 18, cuts: [] },
    subtitles: {
      enabled: true,
      preset: 'tiktok_viral',
      position_y: 0.78,
      lines: [{ id: 'l1', words: [{ w: 'Halo', s: 3.2, e: 3.6 }] }],
    },
    layers: [],
  };
}

describe('projectReducer', () => {
  it('supports adding/updating/removing layers and undo/redo up to 50 steps', () => {
    let st = createHistoryState(sampleProject());
    st = projectReducer(st, {
      type: 'ADD_LAYER',
      layer: {
        id: 't1',
        type: 'title',
        template: 'white_box',
        text: 'Judul',
        x: 0.5,
        y: 0.2,
        scale: 1,
        rotation: 0,
        start: 3,
        end: 6,
      },
    });
    expect(st.present.layers.length).toBe(1);
    expect(st.past.length).toBe(1);

    st = projectReducer(st, {
      type: 'ADD_CUT',
      cut: [6, 8],
    });
    expect(st.present.trim.cuts).toEqual([[6, 8]]);

    st = projectReducer(st, { type: 'REMOVE_CUT', index: 0 });
    expect(st.present.trim.cuts).toEqual([]);

    st = projectReducer(st, { type: 'UNDO' });
    expect(st.present.trim.cuts).toEqual([[6, 8]]);

    st = projectReducer(st, { type: 'REDO' });
    expect(st.present.trim.cuts).toEqual([]);

    // Cap history at 50 entries
    for (let i = 0; i < 60; i += 1) {
      st = projectReducer(st, {
        type: 'SET_TRIM',
        trim: { in: 3, out: 18 - i * 0.05, cuts: [] },
      });
    }
    expect(st.past.length).toBe(50);
  });
});
