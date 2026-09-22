export interface ManualInputRow {
  id: string;
  start: string;
  end: string;
  label: string;
}

export function parseTimeToSeconds(val: string): number {
  const trimmed = val.trim();
  if (trimmed.includes(':')) {
    const parts = trimmed.split(':').map((p) => parseFloat(p));
    if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
      return parts[0] * 60 + parts[1];
    }
    if (parts.length === 3 && !isNaN(parts[0]) && !isNaN(parts[1]) && !isNaN(parts[2])) {
      return parts[0] * 3600 + parts[1] * 60 + parts[2];
    }
  }
  const num = parseFloat(trimmed);
  return isNaN(num) ? 0 : num;
}
