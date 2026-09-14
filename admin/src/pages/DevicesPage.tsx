import { useEffect, useState } from 'react';
import { api, ApiError } from '../lib/api';
import type { AdminDeviceSummary } from '../lib/types';
import { formatDate } from '../lib/format';
import { useToast } from '../components/Toast';

export function DevicesPage() {
  const toast = useToast();
  const [devices, setDevices] = useState<AdminDeviceSummary[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const result = await api.get<{ devices: AdminDeviceSummary[]; totalCount: number }>(
          '/admin/devices',
        );
        setDevices(result.devices);
        setTotalCount(result.totalCount);
      } catch (err) {
        toast.show(err instanceof ApiError ? err.message : 'Failed to load devices.', 'error');
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-[20px] font-extrabold">Installed devices</h1>
        <p className="text-[13px]" style={{ color: 'var(--text-secondary)' }}>
          {totalCount} device{totalCount === 1 ? '' : 's'} currently registered for push
          notifications — includes guests who haven't signed in.
        </p>
      </div>

      <div
        className="overflow-hidden rounded-2xl border"
        style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
      >
        <table className="w-full text-left text-[13px]">
          <thead>
            <tr style={{ color: 'var(--text-secondary)' }}>
              <th className="px-5 py-3 font-semibold">User</th>
              <th className="px-5 py-3 font-semibold">Platform</th>
              <th className="px-5 py-3 font-semibold">App version</th>
              <th className="px-5 py-3 font-semibold">Registered</th>
              <th className="px-5 py-3 font-semibold">Last seen</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={5} className="px-5 py-8 text-center" style={{ color: 'var(--text-secondary)' }}>
                  Loading…
                </td>
              </tr>
            )}
            {!loading && devices.length === 0 && (
              <tr>
                <td colSpan={5} className="px-5 py-8 text-center" style={{ color: 'var(--text-secondary)' }}>
                  No devices registered yet.
                </td>
              </tr>
            )}
            {devices.map((d) => (
              <tr key={d.id} className="border-t" style={{ borderColor: 'var(--divider)' }}>
                <td className="px-5 py-3 font-semibold">
                  {d.userEmail ?? <span style={{ color: 'var(--text-secondary)' }}>Guest</span>}
                </td>
                <td className="px-5 py-3 capitalize">{d.platform ?? '—'}</td>
                <td className="px-5 py-3">{d.appVersion ?? '—'}</td>
                <td className="px-5 py-3">{formatDate(d.createdAt)}</td>
                <td className="px-5 py-3">{formatDate(d.lastSeenAt)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
