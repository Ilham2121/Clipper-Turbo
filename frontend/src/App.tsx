import React, { useState, useEffect, useRef } from 'react';
import type { CreateJobFormData, JobStatusResponse } from './api/client';
import { createJob, getJobStatus, subscribeJobEvents } from './api/client';
import { JobForm } from './components/JobForm';
import { JobProgress } from './components/JobProgress';
import { ClipResults } from './components/ClipResults';
import { SettingsModal } from './components/SettingsModal';
import type { HistoryEntry } from './history';
import { clearHistory, loadHistory, removeEntry, upsertEntry } from './history';
import { clearActiveJob, loadActiveJob, saveActiveJob } from './activeJob';

const GearIcon: React.FC = () => (
  <svg
    width="16"
    height="16"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9c.14.35.41.65.75.85H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </svg>
);

export const App: React.FC = () => {
  // Only a *running* job is worth restoring; finished ones already live in the
  // history drawer. Restoring a completed job is what made the panel/results
  // reappear after every refresh with no way to clear them.
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

  // Cek koneksi backend saat inisialisasi
  useEffect(() => {
    fetch('/api/health')
      .then((res) => {
        if (res.ok) setServerOnline(true);
        else setServerOnline(false);
      })
      .catch(() => setServerOnline(false));
  }, []);

  // Polling status job (AC-9, AC-12)
  useEffect(() => {
    if (!currentJobId) return;

    let isSubscribed = true;

    const poll = async () => {
      try {
        const res = await getJobStatus(currentJobId);
        if (!isSubscribed) return;
        setJobStatus(res);

        // Berhenti polling jika job sudah selesai atau gagal
        if (res.status === 'completed' || res.status === 'failed') {
          return;
        }

        // Jadwalkan polling berikutnya dalam 2 detik
        pollTimerRef.current = window.setTimeout(poll, 2000);
      } catch (err) {
        console.error('Error polling job status:', err);
        if (isSubscribed) {
          // Tetap coba polling lagi setelah 3 detik jika terjadi kendala jaringan sementara
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

  // SSE realtime updates (Iterasi 2.2); polling above stays as fallback path
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

  // Finished jobs must not be restored on the next refresh: their snapshot is
  // already in the history drawer, so the main page starts clean.
  useEffect(() => {
    if (jobStatus?.status === 'completed' || jobStatus?.status === 'failed') {
      clearActiveJob();
    }
  }, [jobStatus?.status]);

  // A restored job may already have finished server-side (e.g. the tab was
  // closed mid-render). Re-check once on mount so a refresh afterwards starts
  // clean while genuinely running jobs still resume.
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
    // Mount-only: re-checking on every render would fight the polling loop.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
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
      // Only a still-running job is worth restoring after refresh.
      if (fresh.status === 'completed' || fresh.status === 'failed') {
        clearActiveJob();
      } else {
        saveActiveJob({ jobId: entry.jobId, youtubeUrl: entry.youtubeUrl || '' });
      }
    } catch {
      // History entries are finished jobs by definition -> nothing to restore.
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

  /** Clear the on-page progress panel + results (history drawer is untouched). */
  const handleClearView = handleReset;

  const isJobCompleted = !!(jobStatus && jobStatus.status === 'completed' && jobStatus.clips && jobStatus.clips.length > 0);
  const isJobRunning = !!(currentJobId && jobStatus && !isJobCompleted && jobStatus.status !== 'failed');

  return (
    <div className="app-container">
      {/* Header */}
      <header className="header">
        <div className="brand">
          <div className="brand-icon">CT</div>
          <div>
            <h1 className="brand-title">Clipper Turbo</h1>
            <p className="brand-subtitle">Automated YouTube Clip Generator</p>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <button
          type="button"
          className="btn-secondary icon-btn"
          onClick={() => setSettingsOpen(true)}
          aria-label="Pengaturan"
          title="Pengaturan: Riwayat, Storage, API Key"
        >
          <GearIcon />
          {history.length > 0 && <span className="icon-btn-badge">{history.length}</span>}
        </button>
        <div className="server-status" title={serverOnline ? 'Backend connected' : 'Backend offline or waiting'}>
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
          <span>
            {serverOnline === true
              ? 'API Aktif'
              : serverOnline === false
              ? 'API Terputus'
              : 'Memeriksa API...'}
          </span>
        </div>
        </div>
      </header>

      {/* Main Content Sections — single page: form on top, process panel below */}
      <main>
        {/* Form View: always visible so users never lose the entry point */}
        <JobForm onSubmit={handleStartJob} isSubmitting={isSubmitting} disabled={isJobRunning} />

        {/* Progress panel + terminal log, rendered below the submit button */}
        <div ref={progressAnchorRef} className="progress-panel-anchor">
          {currentJobId && jobStatus && (
            <JobProgress
              key={jobStatus.jobId}
              status={jobStatus}
              onReset={handleClearView}
              youtubeUrl={currentJobUrl || undefined}
            />
          )}
        </div>

        {/* Results View: stays on the same page, below the progress panel */}
        {isJobCompleted && jobStatus?.clips && (
          <ClipResults clips={jobStatus.clips} onReset={handleClearView} jobId={jobStatus.jobId} />
        )}
      </main>
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

export default App;
