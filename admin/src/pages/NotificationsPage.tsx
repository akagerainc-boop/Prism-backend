import { useEffect, useState, type FormEvent } from 'react';
import { api, ApiError } from '../lib/api';
import type { AdminDeviceSummary, AdminNotificationSendResult } from '../lib/types';
import { useToast } from '../components/Toast';
import { IOSDialog } from '../components/IOSDialog';

export function NotificationsPage() {
  const toast = useToast();
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [imageUrl, setImageUrl] = useState('');
  const [target, setTarget] = useState<'all' | 'device'>('all');
  const [deviceId, setDeviceId] = useState<number | null>(null);
  const [devices, setDevices] = useState<AdminDeviceSummary[]>([]);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const [lastResult, setLastResult] = useState<AdminNotificationSendResult | null>(null);

  useEffect(() => {
    if (target !== 'device' || devices.length > 0) return;
    api
      .get<{ devices: AdminDeviceSummary[] }>('/admin/devices')
      .then((r) => setDevices(r.devices))
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (target === 'device' && deviceId === null) {
      toast.show('Choose a device to send to.', 'error');
      return;
    }
    setConfirmOpen(true);
  }

  async function send() {
    setSending(true);
    try {
      const result = await api.post<AdminNotificationSendResult>('/admin/notifications/send', {
        title: title.trim(),
        body: body.trim(),
        imageUrl: imageUrl.trim() || null,
        target,
        deviceId: target === 'device' ? deviceId : null,
      });
      setLastResult(result);
      toast.show(
        `Sent — ${result.successCount} delivered, ${result.failureCount} failed.`,
        result.failureCount > 0 && result.successCount === 0 ? 'error' : 'success',
      );
      setTitle('');
      setBody('');
      setImageUrl('');
    } catch (err) {
      toast.show(err instanceof ApiError ? err.message : 'Failed to send notification.', 'error');
    } finally {
      setSending(false);
      setConfirmOpen(false);
    }
  }

  return (
    <div className="max-w-lg">
      <div className="mb-6">
        <h1 className="text-[20px] font-extrabold">Send a notification</h1>
        <p className="text-[13px]" style={{ color: 'var(--text-secondary)' }}>
          Delivered as a push through Firebase Cloud Messaging — reaches devices even locked or
          fully closed. Tapping it opens the Notification Detail screen in the app.
        </p>
      </div>

      <form
        onSubmit={onSubmit}
        className="flex flex-col gap-4 rounded-2xl border p-6"
        style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
      >
        <label className="block">
          <span className="mb-1.5 block text-[12px] font-semibold" style={{ color: 'var(--text-secondary)' }}>
            Title
          </span>
          <input
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={255}
            className="w-full rounded-xl border px-3.5 py-2.5 text-[14px] outline-none focus:border-[var(--prism-primary)]"
            style={{ borderColor: 'var(--border)', background: 'var(--bg)', color: 'var(--text-primary)' }}
          />
        </label>

        <label className="block">
          <span className="mb-1.5 block text-[12px] font-semibold" style={{ color: 'var(--text-secondary)' }}>
            Body
          </span>
          <textarea
            required
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={4}
            className="w-full resize-none rounded-xl border px-3.5 py-2.5 text-[14px] outline-none focus:border-[var(--prism-primary)]"
            style={{ borderColor: 'var(--border)', background: 'var(--bg)', color: 'var(--text-primary)' }}
          />
        </label>

        <label className="block">
          <span className="mb-1.5 block text-[12px] font-semibold" style={{ color: 'var(--text-secondary)' }}>
            Image URL (optional)
          </span>
          <input
            value={imageUrl}
            onChange={(e) => setImageUrl(e.target.value)}
            placeholder="https://…"
            className="w-full rounded-xl border px-3.5 py-2.5 text-[14px] outline-none focus:border-[var(--prism-primary)]"
            style={{ borderColor: 'var(--border)', background: 'var(--bg)', color: 'var(--text-primary)' }}
          />
        </label>

        <div className="block">
          <span className="mb-1.5 block text-[12px] font-semibold" style={{ color: 'var(--text-secondary)' }}>
            Target
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setTarget('all')}
              className="flex-1 rounded-xl border py-2.5 text-[13px] font-semibold"
              style={{
                borderColor: target === 'all' ? 'var(--prism-primary)' : 'var(--border)',
                background: target === 'all' ? 'var(--prism-primary)' : 'transparent',
                color: target === 'all' ? '#fff' : 'var(--text-primary)',
              }}
            >
              Every device
            </button>
            <button
              type="button"
              onClick={() => setTarget('device')}
              className="flex-1 rounded-xl border py-2.5 text-[13px] font-semibold"
              style={{
                borderColor: target === 'device' ? 'var(--prism-primary)' : 'var(--border)',
                background: target === 'device' ? 'var(--prism-primary)' : 'transparent',
                color: target === 'device' ? '#fff' : 'var(--text-primary)',
              }}
            >
              One device
            </button>
          </div>
        </div>

        {target === 'device' && (
          <select
            required
            value={deviceId ?? ''}
            onChange={(e) => setDeviceId(Number(e.target.value))}
            className="w-full rounded-xl border px-3.5 py-2.5 text-[14px] outline-none"
            style={{ borderColor: 'var(--border)', background: 'var(--bg)', color: 'var(--text-primary)' }}
          >
            <option value="" disabled>
              Choose a device…
            </option>
            {devices.map((d) => (
              <option key={d.id} value={d.id}>
                {d.userEmail ?? 'Guest'} — {d.platform ?? 'unknown'}
              </option>
            ))}
          </select>
        )}

        <button
          type="submit"
          className="mt-2 rounded-xl py-3 text-[14px] font-bold text-white"
          style={{ background: 'var(--prism-primary)' }}
        >
          Send notification
        </button>
      </form>

      {lastResult && (
        <div
          className="mt-4 rounded-xl border px-4 py-3 text-[12.5px]"
          style={{ borderColor: 'var(--divider)', color: 'var(--text-secondary)' }}
        >
          Last send: {lastResult.successCount} delivered, {lastResult.failureCount} failed.
        </div>
      )}

      <IOSDialog
        open={confirmOpen}
        title="Send this notification?"
        message={
          target === 'all'
            ? 'This will push to every registered device right now.'
            : 'This will push to the selected device right now.'
        }
        onDismiss={() => setConfirmOpen(false)}
        actions={[
          { label: 'Cancel', onClick: () => setConfirmOpen(false) },
          { label: sending ? 'Sending…' : 'Send', isDefault: true, onClick: send },
        ]}
      />
    </div>
  );
}
