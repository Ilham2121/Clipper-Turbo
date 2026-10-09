import React, { useState, useEffect, useRef } from 'react';
import {
  Settings,
  Sparkles,
  Zap,
  Smartphone,
  FileText,
  Activity,
  Wifi,
  WifiOff,
} from 'lucide-react';
import type { CreateJobFormData, JobStatusResponse } from './api/client';
import { createJob, getJobStatus, subscribeJobEvents } from './api/client';
import { JobForm } from './components/JobForm';
import { JobProgress } from './components/JobProgress';
import { ClipResults } from './components/ClipResults';
import { SettingsModal } from './components/SettingsModal';
import { ToastProvider } from './components/Toast';
import type { HistoryEntry } from './history';
import { clearHistory, loadHistory, removeEntry, upsertEntry } from './history';
import { clearActiveJob, loadActiveJob, saveActiveJob } from './activeJob';

export const AppContent: React.FC = () => {
  const restoredJob = loadActiveJob();
  const [currentJobId, setCurrentJobId] = useState<string | null>(restoredJob?.jobId ?? null);
  const [jobStatus, setJobStatus] = useState<JobStatusResponse | null>(
    restoredJob ? { jobId: restoredJob.jobId, status: 'queued', progressPercent: 0, clips: [] } : null,
  );
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [serverOnline, setServerOnline] = useState<boolean | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>(() => loadHistory());
  const [settingsOpen, setSettingsOpen] = useState<boolean>(false);
  const [currentJobUrl, setCurrentJobUrl] = useState<string>(restoredJob?.youtubeUrl ?? '');

  const pollTimerRef = useRef<number | null>(null);
  const lastJobUrlRef = useRef<string>('');
  const progressAnchorRef = useRef<HTMLDivElement>(null);

  // Health check polling backend status
  const checkHealth = () => {
    fetch('/api/health')
      .then((res) => {
        if (res.ok) setServerOnline(true);
        else setServerOnline(false);
      })
      .catch(() => setServerOnline(false));
  };

  useEffect(() => {
    checkHealth();
    const interval = window.setInterval(checkHealth, 20000);
    return () => window.clearInterval(interval);
  }, []);

  // Poll job status (AC-9, AC-12)
  useEffect(() => {
    if (!currentJobId) return;

    let isSubscribed = true;

    const poll = async () => {
      try {
        const res = await getJobStatus(currentJobId);
        if (!isSubscribed) return;
        setJobStatus(res);

        if (res.status === 'completed' || res.status === 'failed') {
          return;
        }

        pollTimerRef.current = window.setTimeout(poll, 2000);
      } catch (err) {
        console.error('Error polling job status:', err);
        if (isSubscribed) {
          pollTimerRef.current = window.setTimeout(poll, 3000);
        }
      }
    };

    poll();

    return () => {
      isSubscribed = false;
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current);
      }
    };
  }, [currentJobId]);

  // SSE realtime updates
  useEffect(() => {
    if (!currentJobId) return;
    return subscribeJobEvents(currentJobId, (res) => setJobStatus(res));
  }, [currentJobId]);

  // Persist completed job snapshots to localStorage history
  useEffect(() => {
    if (jobStatus?.status === 'completed' && jobStatus.clips) {
      setHistory((prev) => {
        if (prev.some((e) => e.jobId === jobStatus.jobId)) return prev;
        return upsertEntry({
          jobId: jobStatus.jobId,
          youtubeUrl: lastJobUrlRef.current,
          createdAt: new Date().toISOString(),
          status: jobStatus.status,
          clips: jobStatus.clips ?? [],
        });
      });
    }
  }, [jobStatus]);

  useEffect(() => {
    if (jobStatus?.status === 'completed' || jobStatus?.status === 'failed') {
      clearActiveJob();
    }
  }, [jobStatus?.status]);

  useEffect(() => {
    if (!restoredJob) return;
    let cancelled = false;
    void getJobStatus(restoredJob.jobId)
      .then((fresh) => {
        if (cancelled) return;
        if (fresh.status === 'completed' || fresh.status === 'failed') {
          clearActiveJob();
        }
        setJobStatus(fresh);
      })
      .catch(() => {
        if (!cancelled) {
          clearActiveJob();
          setCurrentJobId(null);
          setJobStatus(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const scrollToProgress = () => {
    window.setTimeout(() => {
      progressAnchorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 50);
  };

  const handleSelectHistory = async (entry: HistoryEntry) => {
    setSettingsOpen(false);
    setCurrentJobId(entry.jobId);
    setCurrentJobUrl(entry.youtubeUrl || '');
    try {
      const fresh = await getJobStatus(entry.jobId);
      setJobStatus(fresh);
      if (fresh.status === 'completed' || fresh.status === 'failed') {
        clearActiveJob();
      } else {
        saveActiveJob({ jobId: entry.jobId, youtubeUrl: entry.youtubeUrl || '' });
      }
    } catch {
      clearActiveJob();
      setJobStatus({
        jobId: entry.jobId,
        status: 'completed',
        progressPercent: 100,
        clips: entry.clips,
      });
    }
    scrollToProgress();
  };

  const handleStartJob = async (payload: CreateJobFormData) => {
    setIsSubmitting(true);
    try {
      const resp = await createJob(payload);
      lastJobUrlRef.current = payload.youtubeUrl;
      setCurrentJobUrl(payload.youtubeUrl);
      setCurrentJobId(resp.jobId);
      setJobStatus({
        jobId: resp.jobId,
        status: 'queued',
        progressPercent: 0,
        clips: [],
      });
      saveActiveJob({ jobId: resp.jobId, youtubeUrl: payload.youtubeUrl });
      scrollToProgress();
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleReset = () => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
    }
    clearActiveJob();
    setCurrentJobId(null);
    setJobStatus(null);
    setCurrentJobUrl('');
  };

  const handleClearView = handleReset;

  const isJobCompleted = !!(jobStatus && jobStatus.status === 'completed' && jobStatus.clips && jobStatus.clips.length > 0);
  const isJobRunning = !!(currentJobId && jobStatus && !isJobCompleted && jobStatus.status !== 'failed');

  return (
    <div className="app-container">
      {/* Top Header */}
      <header className="header">
        <div className="brand">
          <div className="brand-icon">
            <Sparkles size={20} />
          </div>
          <div>
            <h1 className="brand-title">Clipper Turbo</h1>
            <p className="brand-subtitle">Automated YouTube Studio & AI Reframe</p>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
          <button
            type="button"
            className="btn-secondary icon-btn"
            onClick={() => setSettingsOpen(true)}
            aria-label="Pengaturan Studio"
            title="Pengaturan: Riwayat, Storage, Subtitle, ASR, Diagnostik"
          >
            <Settings size={18} />
            {history.length > 0 && <span className="icon-btn-badge">{history.length}</span>}
          </button>

          <div
            className="server-status"
            onClick={checkHealth}
            style={{ cursor: 'pointer' }}
            title={serverOnline ? 'Backend API Terhubung (Klik untuk ping)' : 'Backend Terputus / Sedang Menghubungkan'}
          >
            <div
              className="status-dot"
              style={{
                backgroundColor:
                  serverOnline === true
                    ? 'var(--status-success)'
                    : serverOnline === false
                    ? 'var(--status-error)'
                    : 'var(--status-warning)',
              }}
            />
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
              {serverOnline === true ? (
                <>
                  <Wifi size={13} color="var(--status-success)" /> API Online
                </>
              ) : serverOnline === false ? (
                <>
                  <WifiOff size={13} color="var(--status-error)" /> Offline
                </>
              ) : (
                <>
                  <Activity size={13} color="var(--status-warning)" /> Memeriksa...
                </>
              )}
            </span>
          </div>
        </div>
      </header>

      {/* Main Workspace — Two-Pane Layout on Desktop (Phase 5 / Note 5) */}
      <main className="workspace-grid">
        {/* Left Column: Job Composer (Sticky) */}
        <aside className="workspace-sidebar">
          <JobForm
            onSubmit={handleStartJob}
            isSubmitting={isSubmitting}
            disabled={isJobRunning}
            onOpenSettings={() => setSettingsOpen(true)}
          />
        </aside>

        {/* Right Column: Live Stepper, Progress Log & Results Gallery */}
        <section className="workspace-content">
          {/* Active Job Progress */}
          {currentJobId && jobStatus && (
            <div ref={progressAnchorRef} className="progress-panel-anchor">
              <JobProgress
                key={jobStatus.jobId}
                status={jobStatus}
                onReset={handleClearView}
                youtubeUrl={currentJobUrl || undefined}
              />
            </div>
          )}

          {/* Results Gallery */}
          {isJobCompleted && jobStatus?.clips && (
            <ClipResults clips={jobStatus.clips} onReset={handleClearView} jobId={jobStatus.jobId} />
          )}

          {/* Studio Empty State when idle */}
          {!currentJobId && (
            <div className="studio-empty-state">
              <div className="studio-empty-icon">
                <Sparkles size={28} />
              </div>
              <h3 className="studio-empty-title">Studio Siap Digunakan</h3>
              <p className="studio-empty-desc">
                Masukkan URL video YouTube di sebelah kiri. Sistem akan mengunduh video, menganalisis transkrip
                dengan AI, melakukan reframing wajah dinamis, dan membakar subtitle animasi per-kata.
              </p>
              <div className="studio-features-list">
                <span className="studio-feature-chip">
                  <Zap size={12} color="var(--accent-primary)" /> Akselerasi HW (NVENC/QSV)
                </span>
                <span className="studio-feature-chip">
                  <Smartphone size={12} color="var(--accent-secondary)" /> Dynamic 9:16 Reframing
                </span>
                <span className="studio-feature-chip">
                  <FileText size={12} color="#10b981" /> Word-Synced Karaoke Subtitles
                </span>
                <span className="studio-feature-chip">
                  <Sparkles size={12} color="#f59e0b" /> Virality Scoring AI
                </span>
              </div>
            </div>
          )}
        </section>
      </main>

      {/* Floating Centered Settings Modal (Note 3 / Phase 4.2) */}
      <SettingsModal
        open={settingsOpen}
        entries={history}
        onSelectHistory={handleSelectHistory}
        onRemoveHistory={(jobId) => setHistory(removeEntry(jobId))}
        onClearHistory={() => setHistory(clearHistory())}
        onClose={() => setSettingsOpen(false)}
      />
    </div>
  );
};

export const App: React.FC = () => {
  return (
    <ToastProvider>
      <AppContent />
    </ToastProvider>
  );
};

export default App;
