import { describe, expect, it } from 'vitest';
import cases from '../../../../backend/tests/fixtures/time_map_cases.json';
import {
  keptRanges,
  mapInterval,
  nextKeptTime,
  outputDuration,
  toMaster,
  toOutput,
} from '../timeMap';
import type { TrimConfig } from '../types';

describe('timeMap shared cross-language fixture', () => {
  for (const c of cases) {
    it(`matches python fixture: ${c.name}`, () => {
      const trim: TrimConfig = {
        in: c.trim.in,
        out: c.trim.out,
        cuts: c.trim.cuts as [number, number][],
      };
      expect(keptRanges(trim)).toEqual(c.kept);
      expect(outputDuration(trim)).toBeCloseTo(c.duration, 5);
      for (const pt of c.points) {
        const got = toOutput(pt.master, trim);
        if (pt.output === null) {
          expect(got).toBeNull();
        } else {
          expect(got).not.toBeNull();
          expect(got!).toBeCloseTo(pt.output, 5);
        }
      }
      for (const iv of c.intervals) {
        const mapped = mapInterval(iv.start, iv.end, trim);
        expect(mapped.length).toBe(iv.mapped.length);
        mapped.forEach((pair, idx) => {
          expect(pair[0]).toBeCloseTo(iv.mapped[idx][0], 5);
          expect(pair[1]).toBeCloseTo(iv.mapped[idx][1], 5);
        });
      }
    });
  }

  it('skips cut ranges via nextKeptTime and inverts via toMaster', () => {
    const trim: TrimConfig = { in: 2, out: 12, cuts: [[4, 6]] };
    expect(nextKeptTime(1.0, trim)).toBe(2);
    expect(nextKeptTime(3.0, trim)).toBe(3);
    expect(nextKeptTime(4.2, trim)).toBe(6);
    expect(nextKeptTime(12.1, trim)).toBeNull();
    expect(toMaster(2.0, trim)).toBeCloseTo(6.0, 5);
  });
});
