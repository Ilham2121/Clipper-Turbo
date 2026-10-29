import type { SubtitleLine, WordItem } from './types';

function round3(n: number): number {
  return Math.round(n * 1000) / 1000;
}

/**
 * Diff `oldWords` against `newText` via token LCS so unchanged words keep their
 * exact `{s, e}` timestamps and replaced/inserted spans divide neighbor ranges evenly.
 */
export function applyLineTextEdit(
  oldWords: WordItem[],
  newText: string,
  fallbackRange: [number, number],
): WordItem[] {
  const tokens = newText.trim().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return [];
  if (oldWords.length === 0) {
    const [rStart, rEnd] = fallbackRange;
    const span = Math.max(0.2, rEnd - rStart);
    const step = span / tokens.length;
    return tokens.map((w, idx) => ({
      w,
      s: round3(rStart + idx * step),
      e: round3(rStart + (idx + 1) * step),
    }));
  }

  const m = oldWords.length;
  const n = tokens.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () =>
    new Array<number>(n + 1).fill(0),
  );

  for (let i = m - 1; i >= 0; i -= 1) {
    for (let j = n - 1; j >= 0; j -= 1) {
      if (oldWords[i].w.toLowerCase() === tokens[j].toLowerCase()) {
        dp[i][j] = 1 + dp[i + 1][j + 1];
      } else {
        dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
  }

  // Anchors: pairs of [oldIdx, newIdx] that matched
  const anchors: [number, number][] = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (oldWords[i].w.toLowerCase() === tokens[j].toLowerCase()) {
      anchors.push([i, j]);
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      i += 1;
    } else {
      j += 1;
    }
  }

  const result: WordItem[] = new Array<WordItem>(n);
  for (const [oldIdx, newIdx] of anchors) {
    result[newIdx] = {
      w: tokens[newIdx],
      s: oldWords[oldIdx].s,
      e: oldWords[oldIdx].e,
    };
  }

  // Fill unmatched new token runs between anchors
  const sentinels: [number, number][] = [[-1, -1], ...anchors, [m, n]];
  for (let k = 0; k < sentinels.length - 1; k += 1) {
    const [oldA, newA] = sentinels[k];
    const [oldB, newB] = sentinels[k + 1];
    const count = newB - newA - 1;
    if (count <= 0) continue;

    let spanStart: number;
    let spanEnd: number;
    if (oldB - oldA > 1) {
      // Replaced one or more old words: reuse their exact combined [start, end]
      spanStart = oldWords[oldA + 1].s;
      spanEnd = oldWords[oldB - 1].e;
    } else if (oldA >= 0 && oldB < m) {
      // Pure insertion between two adjacent kept words
      const gapStart = oldWords[oldA].e;
      const gapEnd = oldWords[oldB].s;
      if (gapEnd - gapStart >= 0.08 * count) {
        spanStart = gapStart;
        spanEnd = gapEnd;
      } else {
        spanStart = gapStart;
        spanEnd = gapStart + 0.16 * count;
      }
    } else if (oldA < 0 && oldB < m) {
      // Prepended before first kept word
      spanEnd = oldWords[oldB].s;
      spanStart = Math.max(0, spanEnd - 0.25 * count);
      if (spanEnd - spanStart < 0.08 * count) {
        spanEnd = spanStart + 0.12 * count;
      }
    } else {
      // Appended after last kept word
      spanStart = oldWords[m - 1].e;
      spanEnd = Math.max(fallbackRange[1], spanStart + 0.25 * count);
    }

    const step = Math.max(0.06, (spanEnd - spanStart) / count);
    for (let offset = 0; offset < count; offset += 1) {
      const targetIdx = newA + 1 + offset;
      result[targetIdx] = {
        w: tokens[targetIdx],
        s: round3(spanStart + offset * step),
        e: round3(spanStart + (offset + 1) * step),
      };
    }
  }

  // Enforce monotonic non-decreasing start timestamps
  for (let idx = 1; idx < result.length; idx += 1) {
    if (result[idx].s < result[idx - 1].s) {
      result[idx].s = result[idx - 1].s;
    }
    if (result[idx].e < result[idx].s + 0.05) {
      result[idx].e = round3(result[idx].s + 0.08);
    }
  }
  return result;
}

export function splitSubtitleLine(
  lines: SubtitleLine[],
  lineIndex: number,
  splitAtWordIndex: number,
): SubtitleLine[] {
  const line = lines[lineIndex];
  if (!line || splitAtWordIndex <= 0 || splitAtWordIndex >= line.words.length) {
    return lines;
  }
  const first: SubtitleLine = {
    id: line.id,
    words: line.words.slice(0, splitAtWordIndex),
  };
  const second: SubtitleLine = {
    id: `${line.id}_b${Date.now().toString(36)}`,
    words: line.words.slice(splitAtWordIndex),
  };
  return [
    ...lines.slice(0, lineIndex),
    first,
    second,
    ...lines.slice(lineIndex + 1),
  ];
}

export function mergeSubtitleLines(
  lines: SubtitleLine[],
  firstIndex: number,
): SubtitleLine[] {
  if (firstIndex < 0 || firstIndex >= lines.length - 1) return lines;
  const a = lines[firstIndex];
  const b = lines[firstIndex + 1];
  const merged: SubtitleLine = {
    id: a.id,
    words: [...a.words, ...b.words],
  };
  return [
    ...lines.slice(0, firstIndex),
    merged,
    ...lines.slice(firstIndex + 2),
  ];
}
