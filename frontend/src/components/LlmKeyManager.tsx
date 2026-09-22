import React, { useCallback, useEffect, useState } from 'react';
import type { LlmKeyStatus, LlmTestResult } from '../api/client';
import { deleteLlmKey, listLlmKeys, saveLlmKey, testLlmKey } from '../api/client';

interface LlmKeyManagerProps {
  provider: string;
  model: string;
  customEndpoint: string;
  disabled?: boolean;
  onHasKeyChange: (hasKey: boolean) => void;
}

export const LlmKeyManager: React.FC<LlmKeyManagerProps> = ({
  provider,
  model,
  customEndpoint,
  disabled,
  onHasKeyChange,
}) => {
  const [status, setStatus] = useState<LlmKeyStatus | null>(null);
  const [editing, setEditing] = useState<boolean>(false);
  const [apiKey, setApiKey] = useState<string>('');
  const [showKey, setShowKey] = useState<boolean>(false);
  const [busy, setBusy] = useState<boolean>(false);
  const [testResult, setTestResult] = useState<LlmTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const all = await listLlmKeys();
      const found = all.find((s) => s.provider === provider) ?? null;
      setStatus(found);
      onHasKeyChange(found?.hasKey ?? false);
      if (!found?.hasKey) setEditing(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Gagal memuat status API key.');
    }
  }, [provider, onHasKeyChange]);

  // Fetch-on-mount; parent remounts via key={provider} on provider change.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleSave = async () => {
    if (!apiKey.trim()) {
      setError('Masukkan API Key terlebih dahulu.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await saveLlmKey(provider, {
        apiKey: apiKey.trim(),
        model: model.trim() || undefined,
        baseUrl: provider === 'custom' ? customEndpoint.trim() || undefined : undefined,
      });
      setApiKey('');
      setEditing(false);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Gagal menyimpan API key.');
    } finally {
      setBusy(false);
    }
  };

  const handleTest = async () => {
    setBusy(true);
    setError(null);
    setTestResult(null);
    try {
      const res = editing && apiKey.trim()
        ? await testLlmKey(provider, {
            apiKey: apiKey.trim(),
            model: model.trim() || undefined,
            baseUrl: provider === 'custom' ? customEndpoint.trim() || undefined : undefined,
          })
        : await testLlmKey(provider);
      setTestResult(res);
      if (!editing) await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Tes koneksi gagal.');
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    if (!window.confirm(`Hapus API key tersimpan untuk ${provider}?`)) return;
    setBusy(true);
    setError(null);
    try {
      await deleteLlmKey(provider);
      setTestResult(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Gagal menghapus API key.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="form-group">
      <label className="form-label">API Key ({provider === 'custom' ? 'Opsional' : 'Sesuai Provider'})</label>

      {error && (
        <div className="banner banner-error"><span>{error}</span></div>
      )}

      {status?.hasKey && !editing ? (
        <div className="llm-key-card">
          <div className="llm-key-info">
            <span className="input-mono">{status.maskedKey ?? '***'}</span>
            {status.lastVerifiedOk === true && <span className="badge badge-ratio">Terverifikasi</span>}
            {status.lastVerifiedOk === false && <span className="badge badge-ratio">Gagal terakhir</span>}
          </div>
          <div className="llm-key-actions">
            <button type="button" className="btn-secondary" onClick={handleTest} disabled={disabled || busy}>
              Tes Koneksi
            </button>
            <button type="button" className="btn-secondary" onClick={() => setEditing(true)} disabled={disabled || busy}>
              Ubah
            </button>
            <button type="button" className="btn-secondary btn-danger" onClick={handleDelete} disabled={disabled || busy}>
              Hapus
            </button>
          </div>
        </div>
      ) : (
        <div className="llm-key-card llm-key-edit">
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <input
              type={showKey ? 'text' : 'password'}
              className="input-text input-mono"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Masukkan API Key..."
              disabled={disabled || busy}
            />
            <button type="button" className="btn-secondary" onClick={() => setShowKey((v) => !v)}>
              {showKey ? 'Sembunyikan' : 'Lihat'}
            </button>
          </div>
          <div className="llm-key-actions">
            <button type="button" className="btn-secondary" onClick={handleSave} disabled={disabled || busy}>
              Simpan
            </button>
            <button type="button" className="btn-secondary" onClick={handleTest} disabled={disabled || busy}>
              Tes Tanpa Simpan
            </button>
            {status?.hasKey && (
              <button type="button" className="btn-secondary" onClick={() => { setEditing(false); setApiKey(''); }} disabled={disabled || busy}>
                Batal
              </button>
            )}
          </div>
        </div>
      )}

      {testResult && (
        <div className={`banner ${testResult.ok ? 'banner-info' : 'banner-error'}`}>
          <span>{testResult.message}</span>
        </div>
      )}

      <p className="form-helper">
        API key disimpan sekali di server (terenkripsi) dan dipakai otomatis untuk job berikutnya.
      </p>
    </div>
  );
};
