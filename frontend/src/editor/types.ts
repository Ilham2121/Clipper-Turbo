export type AspectFormat = '9:16' | '16:9';

export type SubtitlePresetId =
  | 'tiktok_viral'
  | 'classic_karaoke'
  | 'minimalist'
  | 'word_pop';

export type TitleTemplateId =
  | 'white_box'
  | 'yellow_outline'
  | 'keyword_highlight'
  | 'caption_bubble'
  | 'gradient_text'
  | 'headline_2line';

export interface WordItem {
  w: string;
  s: number;
  e: number;
}

export interface SubtitleLine {
  id: string;
  words: WordItem[];
}

export interface SubtitlesConfig {
  enabled: boolean;
  preset: SubtitlePresetId;
  position_y: number;
  lines: SubtitleLine[];
}

export interface TrimConfig {
  in: number;
  out: number;
  cuts: [number, number][];
}

export interface TitleLayer {
  id: string;
  type: 'title';
  template: TitleTemplateId;
  text: string;
  x: number;
  y: number;
  scale: number;
  rotation: number;
  start: number;
  end: number | null;
  png?: string | null;
  pngHash?: string | null;
}

export interface ImageLayer {
  id: string;
  type: 'image';
  src: string;
  x: number;
  y: number;
  scale: number;
  opacity: number;
  start: number;
  end: number | null;
}

export interface VideoLayer {
  id: string;
  type: 'video';
  src: string;
  x: number;
  y: number;
  scale: number;
  opacity: number;
  muted: boolean;
  start: number;
  end: number | null;
}

export type EditorLayer = TitleLayer | ImageLayer | VideoLayer;

export interface ClipProject {
  version: 1;
  trim: TrimConfig;
  subtitles: SubtitlesConfig;
  layers: EditorLayer[];
}

export interface ClipAsset {
  path: string;
  url: string;
  kind: 'image' | 'video';
  name: string;
  width?: number;
  height?: number;
  duration?: number;
}

export interface ProjectApiResponse {
  clipId: string;
  project: ClipProject;
  revision: number;
  masterUrl: string;
  masterDuration: number;
  format: AspectFormat;
  limited: boolean;
  hookText: string;
  assets: ClipAsset[];
}

export interface VersionEntry {
  version: number;
  label: string;
  createdAt: string;
  durationSec: number;
  active: boolean;
}
