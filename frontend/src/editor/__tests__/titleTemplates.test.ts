import { describe, expect, it } from 'vitest';
import {
  TITLE_TEMPLATES,
  computeTitleHash,
  layoutTitle,
} from '../titleTemplates';
import type { TitleLayer } from '../types';

describe('titleTemplates shared layout', () => {
  it('defines all 6 trending templates', () => {
    expect(TITLE_TEMPLATES.map((t) => t.id)).toEqual([
      'white_box',
      'yellow_outline',
      'keyword_highlight',
      'caption_bubble',
      'gradient_text',
      'headline_2line',
    ]);
  });

  it('parses *word* highlights and wraps long lines deterministically', () => {
    const layout = layoutTitle(
      'Ini *rahasia* sukses kreator pemula di TikTok hari ini',
      'keyword_highlight',
      1080,
    );
    expect(layout.lines.length).toBeGreaterThanOrEqual(2);
    const allTokens = layout.lines.flatMap((l) => l.tokens);
    const highlighted = allTokens.filter((t) => t.highlight);
    expect(highlighted.map((t) => t.text)).toEqual(['RAHASIA']);
    expect(layout.boxWidth).toBeGreaterThan(200);
    expect(layout.boxHeight).toBeGreaterThan(80);
  });

  it('changes hash when text, template, scale, or rotation changes but NOT when x/y changes', () => {
    const base: TitleLayer = {
      id: 't1',
      type: 'title',
      template: 'white_box',
      text: 'Halo *dunia*',
      x: 0.5,
      y: 0.2,
      scale: 1,
      rotation: 0,
      start: 0,
      end: 3,
    };
    const moved = { ...base, x: 0.3, y: 0.4 };
    const edited = { ...base, text: 'Halo *semua*' };
    expect(computeTitleHash(base, '9:16')).toBe(computeTitleHash(moved, '9:16'));
    expect(computeTitleHash(base, '9:16')).not.toBe(
      computeTitleHash(edited, '9:16'),
    );
  });
});
