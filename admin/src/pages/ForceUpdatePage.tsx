import { useEffect, useState } from 'react';
import { api, ApiError } from '../lib/api';
import type { AdminAppConfig } from '../lib/types';
import { useToast } from '../components/Toast';
import { IOSDialog } from '../components/IOSDialog';

export function ForceUpdatePage() {
  const toast = useToast();
  const [config, setConfig] = useState<AdminAppConfig | null>(null);
  const [minVersion, setMinVersion] = useState('');
  const [latestVersion, setLatestVersion] = useState('');
  const [playStoreUrl, setPlayStoreUrl] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const result = await api.get<AdminAppConfig>('/admin/app-config');
        setConfig(result);
        setMinVersion(result.minSupportedVersion);
        setLatestVersion(result.latestVersion);
        setPlayStoreUrl(result.playStoreUrl);
      } catch (err) {
        toast.show(err instanceof ApiError ? err.message : 'Failed to load config.', 'error');
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function save() {
    setSaving(true);
    try {
      const result = await api.put<AdminAppConfig>('/admin/app-config', {
        minSupportedVersion: minVersion.trim(),
        latestVersion: latestVersion.trim(),
        playStoreUrl: playStoreUrl.trim(),
      });
      setConfig(result);
      toast.show('Force-update settings saved.', 'success');
    } catch (err) {
      toast.show(err instanceof ApiError ? err.message : 'Failed to save.', 'error');
    } finally {
      setSaving(false);
      setConfirmOpen(false);
    }
  }

  if (loading) {
    return <p style={{ color: 'var(--text-secondary)' }}>Loading…</p>;
  }

  const raisingMinVersion = config !== null && minVersion.trim() !== config.minSupportedVersion;

  return (
    <div className="max-w-lg">
      <div className="mb-6">
        <h1 className="text-[20px] font-extrabold">Force update</h1>
        <p className="text-[13px]" style={{ color: 'var(--text-secondary)' }}>
          Any installed app below the minimum version is blocked on launch with a mandatory
          "Update Now" screen linking to the Play Store.
        </p>
      </div>

      <div
        className="flex flex-col gap-4 rounded-2xl border p-6"
        style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
      >
        <label className="block">
          <span className="mb-1.5 block text-[12px] font-semibold" style={{ color: 'var(--text-secondary)' }}>
            Minimum supported version
          </span>
          <input
            value={minVersion}
            onChange={(e) => setMinVersion(e.target.value)}
            placeholder="1.0.1"
            className="w-full rounded-xl border px-3.5 py-2.5 text-[14px] outline-none focus:border-[var(--prism-primary)]"
            style={{ borderColor: 'var(--border)', background: 'var(--bg)', color: 'var(--text-primary)' }}
          />
        </label>

        <label className="block">
          <span className="mb-1.5 block text-[12px] font-semibold" style={{ color: 'var(--text-secondary)' }}>
            Latest version (informational)
          </span>
          <input
            value={latestVersion}
            onChange={(e) => setLatestVersion(e.target.value)}
            placeholder="1.0.1"
            className="w-full rounded-xl border px-3.5 py-2.5 text-[14px] outline-none focus:border-[var(--prism-primary)]"
            style={{ borderColor: 'var(--border)', background: 'var(--bg)', color: 'var(--text-primary)' }}
          />
        </label>

        <label className="block">
          <span className="mb-1.5 block text-[12px] font-semibold" style={{ color: 'var(--text-secondary)' }}>
            Play Store URL
          </span>
          <input
            value={playStoreUrl}
            onChange={(e) => setPlayStoreUrl(e.target.value)}
            placeholder="https://play.google.com/store/apps/details?id=com.akagerainc.prism"
            className="w-full rounded-xl border px-3.5 py-2.5 text-[14px] outline-none focus:border-[var(--prism-primary)]"
            style={{ borderColor: 'var(--border)', background: 'var(--bg)', color: 'var(--text-primary)' }}
          />
        </label>

        <button
          onClick={() => (raisingMinVersion ? setConfirmOpen(true) : save())}
          disabled={saving}
          className="mt-2 rounded-xl py-3 text-[14px] font-bold text-white transition-opacity disabled:opacity-60"
          style={{ background: 'var(--prism-primary)' }}
        >
          {saving ? 'Saving…' : 'Save changes'}
        </button>
      </div>

      <IOSDialog
        open={confirmOpen}
        title="Force everyone to update?"
        message={`Every installed app below version ${minVersion.trim()} will be blocked on next launch until they update.`}
        icon="⚠️"
        onDismiss={() => setConfirmOpen(false)}
        actions={[
          { label: 'Cancel', onClick: () => setConfirmOpen(false) },
          { label: 'Force update', destructive: true, onClick: save },
        ]}
      />
    </div>
  );
}
