import { describe, expect, it } from 'vitest';
import {
  applyLineTextEdit,
  mergeSubtitleLines,
  splitSubtitleLine,
} from '../wordDiff';
import type { SubtitleLine, WordItem } from '../types';

describe('wordDiff subtitle line editing', () => {
  const base: WordItem[] = [
    { w: 'Halo', s: 3.0, e: 3.4 },
    { w: 'teman', s: 3.4, e: 3.9 },
    { w: 'semua', s: 3.9, e: 4.5 },
  ];

  it('preserves exact timestamps of unchanged words when replacing middle word', () => {
    const res = applyLineTextEdit(base, 'Halo sahabat baik semua', [3.0, 4.5]);
    expect(res.map((x) => x.w)).toEqual(['Halo', 'sahabat', 'baik', 'semua']);
    expect(res[0]).toEqual({ w: 'Halo', s: 3.0, e: 3.4 });
    expect(res[3]).toEqual({ w: 'semua', s: 3.9, e: 4.5 });
    // Replaced span [3.4, 3.9] split evenly across 2 words
    expect(res[1].s).toBeCloseTo(3.4, 3);
    expect(res[1].e).toBeCloseTo(3.65, 3);
    expect(res[2].s).toBeCloseTo(3.65, 3);
    expect(res[2].e).toBeCloseTo(3.9, 3);
  });

  it('preserves unchanged word timestamps when deleting a word', () => {
    const res = applyLineTextEdit(base, 'Halo semua', [3.0, 4.5]);
    expect(res).toEqual([
      { w: 'Halo', s: 3.0, e: 3.4 },
      { w: 'semua', s: 3.9, e: 4.5 },
    ]);
  });

  it('splits and merges subtitle lines cleanly', () => {
    const lines: SubtitleLine[] = [{ id: 'l1', words: base }];
    const split = splitSubtitleLine(lines, 0, 1);
    expect(split.length).toBe(2);
    expect(split[0].words.map((w) => w.w)).toEqual(['Halo']);
    expect(split[1].words.map((w) => w.w)).toEqual(['teman', 'semua']);
    const merged = mergeSubtitleLines(split, 0);
    expect(merged.length).toBe(1);
    expect(merged[0].words).toEqual(base);
  });
});
