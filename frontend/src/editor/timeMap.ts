import type { TrimConfig } from './types';

const EPS = 1e-6;

function normalizedCuts(
  trimIn: number,
  trimOut: number,
  rawCuts: [number, number][] | undefined,
): [number, number][] {
  const items: [number, number][] = [];
  for (const c of rawCuts ?? []) {
    const a = Math.max(trimIn, Number(c[0]));
    const b = Math.min(trimOut, Number(c[1]));
    if (b - a > EPS) {
      items.push([a, b]);
    }
  }
  items.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
  const merged: [number, number][] = [];
  for (const [a, b] of items) {
    const last = merged[merged.length - 1];
    if (last && a <= last[1] + EPS) {
      last[1] = Math.max(last[1], b);
    } else {
      merged.push([a, b]);
    }
  }
  return merged;
}

function round6(n: number): number {
  return Math.round(n * 1e6) / 1e6;
}

export function keptRanges(trim: TrimConfig): [number, number][] {
  const trimIn = Number(trim.in);
  const trimOut = Number(trim.out);
  if (trimOut <= trimIn) return [];
  const cuts = normalizedCuts(trimIn, trimOut, trim.cuts);
  const kept: [number, number][] = [];
  let cursor = trimIn;
  for (const [a, b] of cuts) {
    if (a - cursor > EPS) {
      kept.push([round6(cursor), round6(a)]);
    }
    cursor = Math.max(cursor, b);
  }
  if (trimOut - cursor > EPS) {
    kept.push([round6(cursor), round6(trimOut)]);
  }
  return kept;
}

export function outputDuration(trim: TrimConfig): number {
  return round6(keptRanges(trim).reduce((sum, [a, b]) => sum + (b - a), 0));
}

export function toOutput(t: number, trim: TrimConfig): number | null {
  const ranges = keptRanges(trim);
  if (ranges.length === 0) return null;
  let acc = 0;
  for (let i = 0; i < ranges.length; i += 1) {
    const [a, b] = ranges[i];
    const isLast = i === ranges.length - 1;
    if (t < a - EPS) return null;
    if ((t >= a - EPS && t < b - EPS) || (isLast && Math.abs(t - b) <= EPS)) {
      return round6(Math.max(0, acc + (t - a)));
    }
    if (Math.abs(t - b) <= EPS && !isLast) {
      return null;
    }
    acc += b - a;
  }
  return null;
}

export function toMaster(outT: number, trim: TrimConfig): number {
  const ranges = keptRanges(trim);
  if (ranges.length === 0) return Number(trim.in);
  if (outT <= 0) return ranges[0][0];
  let remaining = outT;
  for (const [a, b] of ranges) {
    const segLen = b - a;
    if (remaining < segLen - EPS) {
      return round6(a + remaining);
    }
    remaining -= segLen;
  }
  return ranges[ranges.length - 1][1];
}

export function mapInterval(
  start: number,
  end: number | null,
  trim: TrimConfig,
): [number, number][] {
  const ranges = keptRanges(trim);
  if (ranges.length === 0) return [];
  const effEnd = end === null ? ranges[ranges.length - 1][1] : Number(end);
  const effStart = Number(start);
  if (effEnd <= effStart + EPS) return [];

  const out: [number, number][] = [];
  let acc = 0;
  for (const [a, b] of ranges) {
    const ovS = Math.max(effStart, a);
    const ovE = Math.min(effEnd, b);
    if (ovE - ovS > EPS) {
      out.push([round6(acc + (ovS - a)), round6(acc + (ovE - a))]);
    }
    acc += b - a;
  }
  return out;
}

/**
 * Given current master video timestamp `t`, return:
 * - `t` if `t` is currently inside a kept range,
 * - the start of the next kept range if `t` is before `trim.in` or inside a cut,
 * - `null` if playback has reached/passed `trim.out`.
 */
export function nextKeptTime(t: number, trim: TrimConfig): number | null {
  const ranges = keptRanges(trim);
  for (const [a, b] of ranges) {
    if (t < a - EPS) return a;
    if (t >= a - EPS && t < b - EPS) return t;
  }
  return null;
}
