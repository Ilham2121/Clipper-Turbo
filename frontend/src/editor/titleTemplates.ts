import type { AspectFormat, TitleLayer, TitleTemplateId } from './types';

export interface TitleTemplateSpec {
  id: TitleTemplateId;
  label: string;
  badge: string;
  fontFamily: 'Montserrat' | 'Inter';
  fontWeight: number;
  fontSize: number;
  lineHeight: number;
  maxCharsPerLine: number;
  uppercase: boolean;
  textColor: string;
  highlightBg: string;
  highlightColor: string;
  boxBg: string | null;
  boxRadius: number;
  paddingX: number;
  paddingY: number;
  strokeColor: string | null;
  strokeWidth: number;
  gradientStops: [string, string] | null;
  hasBubbleTail: boolean;
  twoLineScaleSecond: number;
}

export const TITLE_TEMPLATES: readonly TitleTemplateSpec[] = [
  {
    id: 'white_box',
    label: 'White Box TikTok',
    badge: 'Populer',
    fontFamily: 'Montserrat',
    fontWeight: 800,
    fontSize: 58,
    lineHeight: 1.24,
    maxCharsPerLine: 22,
    uppercase: false,
    textColor: '#0B0B0C',
    highlightBg: '#FFE600',
    highlightColor: '#0B0B0C',
    boxBg: '#FFFFFF',
    boxRadius: 26,
    paddingX: 38,
    paddingY: 26,
    strokeColor: null,
    strokeWidth: 0,
    gradientStops: null,
    hasBubbleTail: false,
    twoLineScaleSecond: 1,
  },
  {
    id: 'yellow_outline',
    label: 'Outline Kuning Tebal',
    badge: 'Shorts',
    fontFamily: 'Montserrat',
    fontWeight: 900,
    fontSize: 64,
    lineHeight: 1.2,
    maxCharsPerLine: 20,
    uppercase: true,
    textColor: '#FFE600',
    highlightBg: '#FF2E54',
    highlightColor: '#FFFFFF',
    boxBg: null,
    boxRadius: 0,
    paddingX: 24,
    paddingY: 18,
    strokeColor: '#000000',
    strokeWidth: 12,
    gradientStops: null,
    hasBubbleTail: false,
    twoLineScaleSecond: 1,
  },
  {
    id: 'keyword_highlight',
    label: 'Highlight Kata Kunci',
    badge: 'Hook',
    fontFamily: 'Montserrat',
    fontWeight: 800,
    fontSize: 60,
    lineHeight: 1.28,
    maxCharsPerLine: 21,
    uppercase: true,
    textColor: '#FFFFFF',
    highlightBg: '#E4FF45',
    highlightColor: '#0B0B0C',
    boxBg: 'rgba(11, 11, 12, 0.72)',
    boxRadius: 22,
    paddingX: 34,
    paddingY: 22,
    strokeColor: '#000000',
    strokeWidth: 6,
    gradientStops: null,
    hasBubbleTail: false,
    twoLineScaleSecond: 1,
  },
  {
    id: 'caption_bubble',
    label: 'Caption Bubble',
    badge: 'Q&A',
    fontFamily: 'Inter',
    fontWeight: 800,
    fontSize: 52,
    lineHeight: 1.26,
    maxCharsPerLine: 24,
    uppercase: false,
    textColor: '#111114',
    highlightBg: '#FF3B5C',
    highlightColor: '#FFFFFF',
    boxBg: '#FFFFFF',
    boxRadius: 28,
    paddingX: 36,
    paddingY: 26,
    strokeColor: null,
    strokeWidth: 0,
    gradientStops: null,
    hasBubbleTail: true,
    twoLineScaleSecond: 1,
  },
  {
    id: 'gradient_text',
    label: 'Gradient Punch',
    badge: 'Tren',
    fontFamily: 'Montserrat',
    fontWeight: 900,
    fontSize: 66,
    lineHeight: 1.2,
    maxCharsPerLine: 19,
    uppercase: true,
    textColor: '#FFFFFF',
    highlightBg: '#FFFFFF',
    highlightColor: '#0B0B0C',
    boxBg: null,
    boxRadius: 0,
    paddingX: 24,
    paddingY: 18,
    strokeColor: '#000000',
    strokeWidth: 11,
    gradientStops: ['#FFE600', '#FF4D6D'],
    hasBubbleTail: false,
    twoLineScaleSecond: 1,
  },
  {
    id: 'headline_2line',
    label: 'Headline 2 Baris',
    badge: 'Cover',
    fontFamily: 'Montserrat',
    fontWeight: 900,
    fontSize: 68,
    lineHeight: 1.18,
    maxCharsPerLine: 18,
    uppercase: true,
    textColor: '#FFFFFF',
    highlightBg: '#FFE600',
    highlightColor: '#0B0B0C',
    boxBg: '#121216',
    boxRadius: 24,
    paddingX: 38,
    paddingY: 26,
    strokeColor: null,
    strokeWidth: 0,
    gradientStops: null,
    hasBubbleTail: false,
    twoLineScaleSecond: 0.68,
  },
] as const;

export function getTitleTemplate(id: TitleTemplateId): TitleTemplateSpec {
  return TITLE_TEMPLATES.find((t) => t.id === id) ?? TITLE_TEMPLATES[0];
}

export interface TitleToken {
  text: string;
  highlight: boolean;
}

export interface TitleLineLayout {
  tokens: TitleToken[];
  fontSize: number;
  lineHeightPx: number;
}

export interface TitleLayout {
  spec: TitleTemplateSpec;
  lines: TitleLineLayout[];
  boxWidth: number;
  boxHeight: number;
}

function parseHighlightTokens(raw: string, uppercase: boolean): TitleToken[] {
  const cleaned = (raw || '').trim() || 'Tulis Judul...';
  const parts = cleaned.split(/\s+/).filter(Boolean);
  const out: TitleToken[] = [];
  for (const part of parts) {
    const isHl = part.length > 2 && part.startsWith('*') && part.endsWith('*');
    const bare = isHl ? part.slice(1, -1) : part;
    const formatted = uppercase ? bare.toUpperCase() : bare;
    if (formatted) {
      out.push({ text: formatted, highlight: isHl });
    }
  }
  return out.length > 0 ? out : [{ text: 'JUDUL', highlight: false }];
}

/**
 * Deterministic word-wrapper & metric calculator shared by BOTH the HTML preview
 * and the HTML5 Canvas PNG exporter (1080-based logical pixels).
 */
export function layoutTitle(
  text: string,
  templateId: TitleTemplateId,
  frameWidth = 1080,
): TitleLayout {
  const spec = getTitleTemplate(templateId);
  const explicitLines = (text || '').split('\n').map((s) => s.trim()).filter(Boolean);
  const rawTokenLines: TitleToken[][] = [];

  if (explicitLines.length > 1) {
    for (const row of explicitLines) {
      rawTokenLines.push(parseHighlightTokens(row, spec.uppercase));
    }
  } else {
    const tokens = parseHighlightTokens(text, spec.uppercase);
    let current: TitleToken[] = [];
    let chars = 0;
    for (const tk of tokens) {
      const nextLen = chars + (current.length > 0 ? 1 : 0) + tk.text.length;
      if (current.length > 0 && nextLen > spec.maxCharsPerLine) {
        rawTokenLines.push(current);
        current = [tk];
        chars = tk.text.length;
      } else {
        current.push(tk);
        chars = nextLen;
      }
    }
    if (current.length > 0) rawTokenLines.push(current);
  }

  const maxBoxW = Math.round(frameWidth * 0.88);
  let widestLinePx = 220;
  const lines: TitleLineLayout[] = rawTokenLines.map((tks, idx) => {
    const scaleFactor = idx > 0 ? spec.twoLineScaleSecond : 1;
    const fontSize = Math.round(spec.fontSize * scaleFactor);
    const lineHeightPx = Math.round(fontSize * spec.lineHeight);
    const approxCharW = fontSize * 0.6;
    const lineChars =
      tks.reduce((acc, t) => acc + t.text.length, 0) + Math.max(0, tks.length - 1);
    const highlightExtra = tks.filter((t) => t.highlight).length * 24;
    widestLinePx = Math.max(
      widestLinePx,
      Math.round(lineChars * approxCharW + highlightExtra),
    );
    return { tokens: tks, fontSize, lineHeightPx };
  });

  const boxWidth = Math.min(maxBoxW, widestLinePx + spec.paddingX * 2);
  const textHeight = lines.reduce((sum, l) => sum + l.lineHeightPx, 0);
  const tailExtra = spec.hasBubbleTail ? 22 : 0;
  const boxHeight = textHeight + spec.paddingY * 2 + tailExtra;

  return { spec, lines, boxWidth, boxHeight };
}

export function computeTitleHash(
  layer: TitleLayer,
  format: AspectFormat,
): string {
  const payload = `${format}|${layer.template}|${layer.scale.toFixed(3)}|${layer.rotation.toFixed(1)}|${layer.text}`;
  let h = 2166136261;
  for (let i = 0; i < payload.length; i += 1) {
    h ^= payload.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(16);
}

function drawRoundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  const rad = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rad, y);
  ctx.arcTo(x + w, y, x + w, y + h, rad);
  ctx.arcTo(x + w, y + h, x, y + h, rad);
  ctx.arcTo(x, y + h, x, y, rad);
  ctx.arcTo(x, y, x + w, y, rad);
  ctx.closePath();
}

export async function renderTitlePng(
  layer: TitleLayer,
  format: AspectFormat,
): Promise<Blob> {
  if (typeof document !== 'undefined' && document.fonts?.ready) {
    await document.fonts.ready;
  }
  const frameW = format === '16:9' ? 1920 : 1080;
  const layout = layoutTitle(layer.text, layer.template, frameW);
  const { spec } = layout;

  // Measure exact line widths with canvas font metrics so background box fits tightly
  const probeCanvas = document.createElement('canvas');
  const probeCtx = probeCanvas.getContext('2d');
  if (!probeCtx) throw new Error('Canvas 2D context tidak tersedia.');

  const measuredLineWidths: number[] = layout.lines.map((line) => {
    probeCtx.font = `${spec.fontWeight} ${line.fontSize}px "${spec.fontFamily}", sans-serif`;
    const spaceW = probeCtx.measureText(' ').width;
    let total = 0;
    line.tokens.forEach((tk, idx) => {
      if (idx > 0) total += spaceW;
      total += probeCtx.measureText(tk.text).width + (tk.highlight ? 24 : 0);
    });
    return total;
  });

  const contentW = Math.max(160, ...measuredLineWidths);
  const baseW = Math.ceil(contentW + spec.paddingX * 2 + spec.strokeWidth * 2);
  const textH = layout.lines.reduce((s, l) => s + l.lineHeightPx, 0);
  const tailH = spec.hasBubbleTail ? 22 : 0;
  const baseH = Math.ceil(textH + spec.paddingY * 2 + tailH + spec.strokeWidth * 2);

  const scale = Math.max(0.1, layer.scale || 1);
  const rad = ((layer.rotation || 0) * Math.PI) / 180;
  const cos = Math.abs(Math.cos(rad));
  const sin = Math.abs(Math.sin(rad));
  const scaledW = baseW * scale;
  const scaledH = baseH * scale;
  const canvasW = Math.max(32, Math.ceil(scaledW * cos + scaledH * sin + 16));
  const canvasH = Math.max(32, Math.ceil(scaledW * sin + scaledH * cos + 16));

  const canvas = document.createElement('canvas');
  canvas.width = canvasW;
  canvas.height = canvasH;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Canvas 2D context gagal dibuat.');

  ctx.save();
  ctx.translate(canvasW / 2, canvasH / 2);
  ctx.rotate(rad);
  ctx.scale(scale, scale);
  ctx.translate(-baseW / 2, -baseH / 2);

  const boxH = baseH - tailH;
  if (spec.boxBg) {
    ctx.save();
    ctx.shadowColor = 'rgba(0, 0, 0, 0.35)';
    ctx.shadowBlur = 24;
    ctx.shadowOffsetY = 8;
    ctx.fillStyle = spec.boxBg;
    drawRoundedRect(ctx, 0, 0, baseW, boxH, spec.boxRadius);
    ctx.fill();
    if (spec.hasBubbleTail) {
      ctx.beginPath();
      ctx.moveTo(46, boxH - 2);
      ctx.lineTo(64, boxH + tailH);
      ctx.lineTo(86, boxH - 2);
      ctx.closePath();
      ctx.fill();
    }
    ctx.restore();
  }

  let cursorY = spec.paddingY + spec.strokeWidth;
  layout.lines.forEach((line, lineIdx) => {
    ctx.font = `${spec.fontWeight} ${line.fontSize}px "${spec.fontFamily}", sans-serif`;
    ctx.textBaseline = 'middle';
    ctx.lineJoin = 'round';
    const spaceW = ctx.measureText(' ').width;
    const lineW = measuredLineWidths[lineIdx];
    let cursorX = (baseW - lineW) / 2;
    const midY = cursorY + line.lineHeightPx / 2;

    line.tokens.forEach((tk, tkIdx) => {
      if (tkIdx > 0) cursorX += spaceW;
      const m = ctx.measureText(tk.text);
      const padHl = tk.highlight ? 12 : 0;
      const tokenBoxW = m.width + padHl * 2;

      if (tk.highlight) {
        ctx.fillStyle = spec.highlightBg;
        drawRoundedRect(
          ctx,
          cursorX,
          midY - line.fontSize * 0.58,
          tokenBoxW,
          line.fontSize * 1.16,
          12,
        );
        ctx.fill();
      }

      const drawX = cursorX + padHl;
      if (spec.strokeColor && spec.strokeWidth > 0 && !tk.highlight) {
        ctx.strokeStyle = spec.strokeColor;
        ctx.lineWidth = spec.strokeWidth;
        ctx.strokeText(tk.text, drawX, midY);
      }

      if (spec.gradientStops && !tk.highlight) {
        const grad = ctx.createLinearGradient(
          0,
          midY - line.fontSize / 2,
          0,
          midY + line.fontSize / 2,
        );
        grad.addColorStop(0, spec.gradientStops[0]);
        grad.addColorStop(1, spec.gradientStops[1]);
        ctx.fillStyle = grad;
      } else {
        ctx.fillStyle = tk.highlight ? spec.highlightColor : spec.textColor;
      }
      ctx.fillText(tk.text, drawX, midY);
      cursorX += tokenBoxW;
    });

    cursorY += line.lineHeightPx;
  });

  ctx.restore();

  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error('Gagal mengekspor canvas judul ke PNG.'));
    }, 'image/png');
  });
}
