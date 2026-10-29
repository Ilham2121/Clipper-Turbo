import React, { useState, useEffect, useRef } from 'react';
import {
  Settings2,
  Sparkles,
  Film,
  Clock,
  Subtitles,
  Activity,
  Wifi,
  WifiOff,
} from 'lucide-react';
import type { CreateJobFormData, JobStatusResponse } from './api/client';
import { createJob, getJobStatus, isJobNotFoundError, subscribeJobEvents } from './api/client';
import { JobForm } from './components/JobForm';
import { JobProgress } from './components/JobProgress';
import { ClipResults } from './components/ClipResults';
import type { SettingsTab } from './components/SettingsModal';
import { SettingsModal } from './components/SettingsModal';
import { ToastProvider, useToast } from './components/Toast';
import type { ParsedJobError } from './api/errorParser';
import { parseJobError } from './api/errorParser';
import type { HistoryEntry } from './history';
import { clearHistory, loadHistory, removeEntry, upsertEntry } from './history';
import { clearActiveJob, loadActiveJob, saveActiveJob } from './activeJob';
import { ClipEditorPage } from './editor/ClipEditorPage';

const AppContent: React.FC = () => {
  const toast = useToast();
  const restoredJob = loadActiveJob();
  const [currentJobId, setCurrentJobId] = useState<string | null>(restoredJob?.jobId ?? null);
  const [jobStatus, setJobStatus] = useState<JobStatusResponse | null>(
    restoredJob ? { jobId: restoredJob.jobId, status: 'queued', progressPercent: 0, clips: [] } : null,
  );
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [serverOnline, setServerOnline] = useState<boolean | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>(() => loadHistory());
  const [settingsOpen, setSettingsOpen] = useState<boolean>(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>('subtitle');
  const [currentJobUrl, setCurrentJobUrl] = useState<string>(restoredJob?.youtubeUrl ?? '');
  const [failedJobError, setFailedJobError] = useState<ParsedJobError | null>(null);
  const [editingClipId, setEditingClipId] = useState<string | null>(null);
  const [clipCacheBust, setClipCacheBust] = useState<number>(0);

  const pollTimerRef = useRef<number | null>(null);
  const lastJobUrlRef = useRef<string>('');
  const lastJobTitleRef = useRef<string>('');
  const progressAnchorRef = useRef<HTMLDivElement>(null);
  const notifiedFailedJobIdRef = useRef<string | null>(null);

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

  // Poll job status
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
        if (!isSubscribed) return;
        if (isJobNotFoundError(err)) {
          // Job benar-benar hilang dari server -> hentikan polling,
          // tampilkan info transien di status eksisiting.
          console.warn('Job tidak ditemukan di server (404), polling dihentikan.');
          setJobStatus((prev) =>
            prev ?? { jobId: currentJobId, status: 'failed', progressPercent: 0, clips: [] },
          );
          clearActiveJob();
          return;
        }
        console.error('Error polling job status:', err);
        pollTimerRef.current = window.setTimeout(poll, 3000);
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
          title: lastJobTitleRef.current || undefined,
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

  // Trigger failure toast once per failed job
  useEffect(() => {
    if (jobStatus?.status === 'failed' && currentJobId) {
      if (notifiedFailedJobIdRef.current !== currentJobId) {
        notifiedFailedJobIdRef.current = currentJobId;
        const parsed = parseJobError(jobStatus.error, jobStatus.stage || undefined);
        toast.error(`Job Gagal: ${parsed.title}`, parsed.friendlyMessage);
      }
    }
  }, [jobStatus?.status, jobStatus?.error, jobStatus?.stage, currentJobId, toast]);

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
      .catch((err) => {
        // 404 = job benar-benar hilang (dihapus/TTL) -> reset ke tampilan kosong.
        // Error lain (koneksi/server 5xx) = transien -> pertahankan job & coba lagi.
        if (cancelled) return;
        if (isJobNotFoundError(err)) {
          clearActiveJob();
          setCurrentJobId(null);
          setJobStatus(null);
          return;
        }
        setJobStatus((prev) =>
          prev ?? { jobId: restoredJob.jobId, status: 'queued', progressPercent: 0, clips: [] },
        );
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
    setFailedJobError(null);
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

  const handleStartJob = async (payload: CreateJobFormData, meta?: { videoTitle?: string }) => {
    setIsSubmitting(true);
    setFailedJobError(null);
    try {
      const resp = await createJob(payload);
      lastJobUrlRef.current = payload.youtubeUrl;
      lastJobTitleRef.current = meta?.videoTitle ?? '';
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
    } catch (err: any) {
      const errMsg = err?.message || 'Gagal memulai job baru.';
      toast.error('Gagal Memulai Job', errMsg);
      setFailedJobError({
        stage: 'system',
        stageLabel: 'Inisialisasi Job',
        title: 'Gagal Memulai Job',
        friendlyMessage: errMsg,
        technicalDetail: String(err),
        suggestion: 'Pastikan koneksi server backend Clipper Turbo aktif dan parameter video valid.',
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRetryJob = () => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
    }
    clearActiveJob();
    if (jobStatus?.status === 'failed') {
      const parsed = parseJobError(jobStatus.error, jobStatus.stage || undefined);
      setFailedJobError(parsed);
    }
    setCurrentJobId(null);
    setJobStatus(null);
  };

  const handleReset = () => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
    }
    clearActiveJob();
    setCurrentJobId(null);
    setJobStatus(null);
    setCurrentJobUrl('');
    setFailedJobError(null);
  };

  const handleClearView = handleReset;

  const handleOpenSettingsTab = (tab: SettingsTab) => {
    setSettingsTab(tab);
    setSettingsOpen(true);
  };

  const handleClipUpdated = async () => {
    setClipCacheBust(Date.now());
    if (currentJobId) {
      try {
        const fresh = await getJobStatus(currentJobId);
        setJobStatus(fresh);
      } catch {
        // ignore
      }
    }
  };

  const isJobCompleted = !!(jobStatus && jobStatus.status === 'completed' && jobStatus.clips && jobStatus.clips.length > 0);
  const showJobProgress = !!(currentJobId && jobStatus && !isJobCompleted);
  const isJobRunning = !!(showJobProgress && jobStatus?.status !== 'failed');

  if (editingClipId) {
    return (
      <ClipEditorPage
        clipId={editingClipId}
        onClose={() => setEditingClipId(null)}
        onClipUpdated={() => void handleClipUpdated()}
      />
    );
  }

  return (
    <div className="app-container">
      {/* Top Header with Modern SaaS Navigation Tabs */}
      <header className="header">
        <div className="brand">
          <div className="brand-icon">
            <Sparkles size={20} />
          </div>
          <div>
            <h1 className="brand-title">Clipper Turbo</h1>
            <p className="brand-subtitle">Clip video viral. Langsung dari laptop kamu.</p>
          </div>
        </div>

        {/* Central SaaS Nav Tabs */}
        <nav className="nav-tabs" role="tablist">
          <button
            type="button"
            className="nav-tab-btn active"
            onClick={handleClearView}
            title="Studio Pembuatan Klip"
          >
            <Film size={15} />
            <span>Studio</span>
          </button>
          <button
            type="button"
            className="nav-tab-btn"
            onClick={() => handleOpenSettingsTab('history')}
            title="Riwayat Klip & Proyek"
          >
            <Clock size={15} />
            <span>Riwayat & Proyek</span>
            {history.length > 0 && <span className="nav-tab-badge">{history.length}</span>}
          </button>
          <button
            type="button"
            className="nav-tab-btn"
            onClick={() => handleOpenSettingsTab('subtitle')}
            title="Kustomisasi Gaya & Animasi Subtitle"
          >
            <Subtitles size={15} />
            <span>Subtitle Studio</span>
          </button>
          <button
            type="button"
            className="nav-tab-btn"
            onClick={() => handleOpenSettingsTab('keys')}
            title="Pengaturan API Key, Storage, dan GPU"
          >
            <Settings2 size={15} />
            <span>Pengaturan & GPU</span>
          </button>
        </nav>

        {/* Right Status */}
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
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

      {/* Main Full-Width Studio Workspace */}
      <main style={{ minHeight: '60vh' }}>
        {/* State 1: Active Processing or Failed Mission Control */}
        {showJobProgress && jobStatus && (
          <div ref={progressAnchorRef} className="mission-control-box">
            <JobProgress
              key={jobStatus.jobId}
              status={jobStatus}
              onReset={handleReset}
              onRetry={handleRetryJob}
              onOpenSettings={(tab) => handleOpenSettingsTab(tab as SettingsTab)}
              youtubeUrl={currentJobUrl || undefined}
            />
          </div>
        )}

        {/* State 2: Results Gallery Showcase */}
        {isJobCompleted && jobStatus?.clips && (
          <div style={{ animation: 'fadeIn 0.25s ease-out' }}>
            <ClipResults
              clips={jobStatus.clips}
              onReset={handleReset}
              onEditClip={setEditingClipId}
              jobId={jobStatus.jobId}
              cacheBust={clipCacheBust}
            />
          </div>
        )}

        {/* State 3: Creation Studio Console (when idle or retrying/resetting) */}
        {!showJobProgress && !isJobCompleted && (
          <JobForm
            onSubmit={handleStartJob}
            isSubmitting={isSubmitting}
            disabled={isJobRunning}
            onOpenSettings={() => handleOpenSettingsTab('keys')}
            onOpenSubtitles={() => handleOpenSettingsTab('subtitle')}
            initialUrl={currentJobUrl}
            failedJobError={failedJobError}
            onDismissFailedError={() => setFailedJobError(null)}
          />
        )}
      </main>

      {/* Settings, Subtitles, History & Diagnostics Modal */}
      <SettingsModal
        key={settingsTab}
        open={settingsOpen}
        initialTab={settingsTab}
        entries={history}
        onSelectHistory={handleSelectHistory}
        onRemoveHistory={(jobId) => setHistory(removeEntry(jobId))}
        onClearHistory={() => setHistory(clearHistory())}
        onClose={() => setSettingsOpen(false)}
      />
    </div>
  );
};

const App: React.FC = () => {
  return (
    <ToastProvider>
      <AppContent />
    </ToastProvider>
  );
};

export default App;
