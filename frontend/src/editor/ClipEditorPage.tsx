import React, { useCallback, useEffect, useState } from 'react';
import {
  ArrowLeft,
  Image as ImageIcon,
  Layers,
  Redo2,
  Scissors,
  Subtitles,
  Type,
  Undo2,
} from 'lucide-react';
import './editor.css';
import { ExportStatus } from './ExportStatus';
import { PreviewStage } from './PreviewStage';
import { PropertiesPanel } from './PropertiesPanel';
import { ThumbnailDialog } from './ThumbnailDialog';
import { Timeline } from './Timeline';
import { OverlayTool } from './tools/OverlayTool';
import { SubtitleTool } from './tools/SubtitleTool';
import { TitleTool } from './tools/TitleTool';
import { TrimTool } from './tools/TrimTool';
import { useProject } from './useProject';

type ToolTab = 'title' | 'subtitle' | 'overlay' | 'trim';

interface ClipEditorPageProps {
  clipId: string;
  onClose: () => void;
  onClipUpdated: () => void;
}

export const ClipEditorPage: React.FC<ClipEditorPageProps> = ({
  clipId,
  onClose,
  onClipUpdated,
}) => {
  const {
    loading,
    error,
    project,
    masterUrl,
    masterDuration,
    format,
    limited,
    hookText,
    assets,
    setAssets,
    saveStatus,
    saveError,
    conflict,
    dispatch,
    canUndo,
    canRedo,
    undo,
    redo,
    reload,
    forceOverwrite,
    flushSave,
  } = useProject(clipId);

  const [tab, setTab] = useState<ToolTab>('title');
  const [currentTime, setCurrentTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [selectedLayerId, setSelectedLayerId] = useState<string | null>(null);
  const [splitMarks, setSplitMarks] = useState<number[]>([]);
  const [selectedCutRange, setSelectedCutRange] = useState<[number, number] | null>(
    null,
  );
  const [thumbOpen, setThumbOpen] = useState(false);

  // Switch default tab to trim when clip opens in limited mode
  useEffect(() => {
    if (!loading) {
      setCurrentTime(project.trim.in);
      if (limited) setTab('trim');
    }
  }, [loading, limited, project.trim.in]);

  const addSplitAtPlayhead = useCallback(() => {
    if (
      currentTime <= project.trim.in + 0.08 ||
      currentTime >= project.trim.out - 0.08
    ) {
      return;
    }
    setSplitMarks((prev) =>
      prev.some((m) => Math.abs(m - currentTime) < 0.05)
        ? prev
        : [...prev, Math.round(currentTime * 100) / 100].sort((a, b) => a - b),
    );
  }, [currentTime, project.trim.in, project.trim.out]);

  const deleteSelectedRange = useCallback(() => {
    if (!selectedCutRange) return;
    dispatch({ type: 'ADD_CUT', cut: selectedCutRange });
    setSelectedCutRange(null);
    setSplitMarks([]);
  }, [selectedCutRange, dispatch]);

  // Global keyboard shortcuts (ignored when typing inside inputs/textareas)
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
        return;
      }
      if (e.code === 'Space') {
        e.preventDefault();
        setPlaying((p) => !p);
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        const step = e.shiftKey ? 1.0 : 1 / 30;
        setCurrentTime((t) => Math.max(0, t - step));
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        const step = e.shiftKey ? 1.0 : 1 / 30;
        setCurrentTime((t) => Math.min(masterDuration, t + step));
      } else if (e.key.toLowerCase() === 's') {
        e.preventDefault();
        addSplitAtPlayhead();
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selectedCutRange) {
          e.preventDefault();
          deleteSelectedRange();
        } else if (selectedLayerId) {
          e.preventDefault();
          dispatch({ type: 'REMOVE_LAYER', id: selectedLayerId });
          setSelectedLayerId(null);
        }
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [
    undo,
    redo,
    masterDuration,
    addSplitAtPlayhead,
    selectedCutRange,
    deleteSelectedRange,
    selectedLayerId,
    dispatch,
  ]);

  if (loading) {
    return (
      <div className="ce-page" style={{ placeItems: 'center', display: 'grid' }}>
        <span>Memuat Editor Klip...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="ce-page" style={{ placeItems: 'center', display: 'grid' }}>
        <div style={{ textAlign: 'center', display: 'grid', gap: '0.75rem' }}>
          <p style={{ color: 'var(--danger)' }}>{error}</p>
          <button type="button" className="btn-secondary" onClick={onClose}>
            Kembali ke Galeri
          </button>
        </div>
      </div>
    );
  }

  const statusColor =
    saveStatus === 'saved'
      ? 'var(--success)'
      : saveStatus === 'saving'
        ? 'var(--warning)'
        : 'var(--danger)';
  const statusText =
    saveStatus === 'saved'
      ? 'Tersimpan'
      : saveStatus === 'saving'
        ? 'Menyimpan...'
        : saveStatus === 'conflict'
          ? 'Konflik Revisi'
          : `Belum tersimpan${saveError ? ` (${saveError})` : ''}`;

  return (
    <div className="ce-page">
      {/* Header */}
      <header className="ce-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <button
            type="button"
            className="btn-secondary"
            style={{ padding: '0.35rem 0.7rem', fontSize: '0.8rem' }}
            onClick={onClose}
          >
            <ArrowLeft size={14} /> Kembali
          </button>
          <strong style={{ fontSize: '0.9rem' }}>
            Editor Klip ({clipId.slice(0, 8)})
          </strong>
          <span
            style={{
              fontSize: '0.75rem',
              color: statusColor,
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.3rem',
            }}
          >
            ● {statusText}
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <button
            type="button"
            className="btn-secondary"
            style={{ padding: '0.35rem 0.55rem' }}
            disabled={!canUndo}
            onClick={undo}
            title="Undo (Ctrl+Z)"
          >
            <Undo2 size={14} />
          </button>
          <button
            type="button"
            className="btn-secondary"
            style={{ padding: '0.35rem 0.55rem' }}
            disabled={!canRedo}
            onClick={redo}
            title="Redo (Ctrl+Shift+Z)"
          >
            <Redo2 size={14} />
          </button>
          <button
            type="button"
            className="btn-secondary"
            style={{ padding: '0.4rem 0.75rem', fontSize: '0.78rem' }}
            onClick={() => setThumbOpen(true)}
          >
            <ImageIcon size={14} /> Thumbnail
          </button>
          <ExportStatus
            clipId={clipId}
            project={project}
            format={format}
            flushSave={flushSave}
            dispatch={dispatch}
            onClipUpdated={onClipUpdated}
          />
        </div>
      </header>

      {/* Banners Row */}
      <div>
        {limited && (
          <div className="ce-banner">
            <span>
              Klip ini dibuat sebelum Editor v2. Subtitle sudah ter-burn di dalam
              video; hanya fitur Trim dan Overlay yang tersedia.
            </span>
          </div>
        )}
        {conflict && (
          <div className="ce-banner ce-banner-danger">
            <span>Ada perubahan dari tab atau sesi lain (konflik revisi).</span>
            <div style={{ display: 'flex', gap: '0.4rem' }}>
              <button
                type="button"
                className="btn-secondary"
                style={{ padding: '0.2rem 0.55rem', fontSize: '0.74rem' }}
                onClick={() => void reload()}
              >
                Muat ulang
              </button>
              <button
                type="button"
                className="btn-primary"
                style={{ width: 'auto', padding: '0.2rem 0.65rem', fontSize: '0.74rem' }}
                onClick={() => void forceOverwrite()}
              >
                Timpa dengan versi saya
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Main 3-column workspace */}
      <div className="ce-workspace">
        <div className="ce-left">
          <nav className="ce-rail">
            <button
              type="button"
              disabled={limited}
              className={`ce-rail-btn ${tab === 'title' ? 'active' : ''}`}
              onClick={() => setTab('title')}
            >
              <Type size={17} />
              <span>Judul</span>
            </button>
            <button
              type="button"
              disabled={limited}
              className={`ce-rail-btn ${tab === 'subtitle' ? 'active' : ''}`}
              onClick={() => setTab('subtitle')}
            >
              <Subtitles size={17} />
              <span>Subtitle</span>
            </button>
            <button
              type="button"
              className={`ce-rail-btn ${tab === 'overlay' ? 'active' : ''}`}
              onClick={() => setTab('overlay')}
            >
              <Layers size={17} />
              <span>Overlay</span>
            </button>
            <button
              type="button"
              className={`ce-rail-btn ${tab === 'trim' ? 'active' : ''}`}
              onClick={() => setTab('trim')}
            >
              <Scissors size={17} />
              <span>Trim</span>
            </button>
          </nav>

          <div className="ce-tool-body">
            {tab === 'title' && !limited && (
              <TitleTool
                project={project}
                hookText={hookText}
                currentTime={currentTime}
                onSelectLayer={setSelectedLayerId}
                dispatch={dispatch}
              />
            )}
            {tab === 'subtitle' && !limited && (
              <SubtitleTool
                project={project}
                onSeek={setCurrentTime}
                dispatch={dispatch}
              />
            )}
            {tab === 'overlay' && (
              <OverlayTool
                clipId={clipId}
                project={project}
                assets={assets}
                currentTime={currentTime}
                onAssetUploaded={(a) => setAssets((prev) => [...prev, a])}
                onSelectLayer={setSelectedLayerId}
                dispatch={dispatch}
              />
            )}
            {tab === 'trim' && (
              <TrimTool
                project={project}
                masterDuration={masterDuration}
                dispatch={dispatch}
              />
            )}
          </div>
        </div>

        <PreviewStage
          clipId={clipId}
          masterUrl={masterUrl}
          format={format}
          project={project}
          currentTime={currentTime}
          playing={playing}
          selectedLayerId={selectedLayerId}
          onTimeChange={setCurrentTime}
          onPlayingChange={setPlaying}
          onSelectLayer={setSelectedLayerId}
          dispatch={dispatch}
        />

        <PropertiesPanel
          project={project}
          masterDuration={masterDuration}
          selectedLayerId={selectedLayerId}
          onSelectLayer={setSelectedLayerId}
          dispatch={dispatch}
        />
      </div>

      {/* Bottom Timeline */}
      <Timeline
        project={project}
        masterDuration={masterDuration}
        currentTime={currentTime}
        splitMarks={splitMarks}
        selectedCutRange={selectedCutRange}
        selectedLayerId={selectedLayerId}
        onSeek={(t) => {
          setPlaying(false);
          setCurrentTime(t);
        }}
        onAddSplitAtPlayhead={addSplitAtPlayhead}
        onSelectCutRange={setSelectedCutRange}
        onDeleteSelectedRange={deleteSelectedRange}
        onSelectLayer={setSelectedLayerId}
        dispatch={dispatch}
      />

      {thumbOpen && (
        <ThumbnailDialog
          clipId={clipId}
          project={project}
          format={format}
          initialTime={currentTime}
          flushSave={flushSave}
          dispatch={dispatch}
          onClose={() => setThumbOpen(false)}
        />
      )}
    </div>
  );
};
