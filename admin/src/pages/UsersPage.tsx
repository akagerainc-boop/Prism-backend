import { useEffect, useState } from 'react';
import { api, ApiError } from '../lib/api';
import type { AdminUserDetail, AdminUserSummary } from '../lib/types';
import { formatBytes, formatDate } from '../lib/format';
import { IOSDialog } from '../components/IOSDialog';
import { useToast } from '../components/Toast';

export function UsersPage() {
  const toast = useToast();
  const [users, setUsers] = useState<AdminUserSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<AdminUserDetail | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<AdminUserSummary | null>(null);

  async function load(query?: string) {
    setLoading(true);
    try {
      const qs = query ? `?search=${encodeURIComponent(query)}` : '';
      const result = await api.get<{ users: AdminUserSummary[] }>(`/admin/users${qs}`);
      setUsers(result.users);
    } catch (err) {
      toast.show(err instanceof ApiError ? err.message : 'Failed to load users.', 'error');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function openUser(id: number) {
    try {
      const detail = await api.get<AdminUserDetail>(`/admin/users/${id}`);
      setSelected(detail);
    } catch (err) {
      toast.show(err instanceof ApiError ? err.message : 'Failed to load user.', 'error');
    }
  }

  async function toggleActive(user: AdminUserSummary) {
    try {
      const updated = await api.patch<AdminUserSummary>(`/admin/users/${user.id}/active`, {
        isActive: !user.isActive,
      });
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
      if (selected?.user.id === updated.id) {
        setSelected({ ...selected, user: updated });
      }
      toast.show(updated.isActive ? 'User activated.' : 'User deactivated.', 'success');
    } catch (err) {
      toast.show(err instanceof ApiError ? err.message : 'Failed to update user.', 'error');
    } finally {
      setConfirmTarget(null);
    }
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-[20px] font-extrabold">Users</h1>
          <p className="text-[13px]" style={{ color: 'var(--text-secondary)' }}>
            {users.length} account{users.length === 1 ? '' : 's'}
          </p>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            load(search);
          }}
        >
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by email…"
            className="w-64 rounded-xl border px-3.5 py-2 text-[13px] outline-none focus:border-[var(--prism-primary)]"
            style={{ borderColor: 'var(--border)', background: 'var(--surface)' }}
          />
        </form>
      </div>

      <div
        className="overflow-hidden rounded-2xl border"
        style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
      >
        <table className="w-full text-left text-[13px]">
          <thead>
            <tr style={{ color: 'var(--text-secondary)' }}>
              <th className="px-5 py-3 font-semibold">Email</th>
              <th className="px-5 py-3 font-semibold">Plan</th>
              <th className="px-5 py-3 font-semibold">Storage</th>
              <th className="px-5 py-3 font-semibold">Documents</th>
              <th className="px-5 py-3 font-semibold">Joined</th>
              <th className="px-5 py-3 font-semibold">Status</th>
              <th className="px-5 py-3" />
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={7} className="px-5 py-8 text-center" style={{ color: 'var(--text-secondary)' }}>
                  Loading…
                </td>
              </tr>
            )}
            {!loading && users.length === 0 && (
              <tr>
                <td colSpan={7} className="px-5 py-8 text-center" style={{ color: 'var(--text-secondary)' }}>
                  No users yet.
                </td>
              </tr>
            )}
            {users.map((u) => (
              <tr
                key={u.id}
                className="cursor-pointer border-t hover:bg-black/[0.02] dark:hover:bg-white/[0.03]"
                style={{ borderColor: 'var(--divider)' }}
                onClick={() => openUser(u.id)}
              >
                <td className="px-5 py-3 font-semibold">{u.email}</td>
                <td className="px-5 py-3 capitalize">{u.plan}</td>
                <td className="px-5 py-3">
                  {formatBytes(u.storageUsedBytes)} / {formatBytes(u.storageLimitBytes)}
                </td>
                <td className="px-5 py-3">{u.documentCount}</td>
                <td className="px-5 py-3">{formatDate(u.createdAt)}</td>
                <td className="px-5 py-3">
                  <span
                    className="rounded-full px-2.5 py-1 text-[11px] font-bold"
                    style={{
                      background: u.isActive ? '#2e7d3220' : '#c5363620',
                      color: u.isActive ? '#2e7d32' : '#c53636',
                    }}
                  >
                    {u.isActive ? 'Active' : 'Disabled'}
                  </span>
                </td>
                <td className="px-5 py-3 text-right">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setConfirmTarget(u);
                    }}
                    className="text-[12px] font-semibold text-[var(--prism-primary)] hover:underline"
                  >
                    {u.isActive ? 'Deactivate' : 'Activate'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected && (
        <div
          className="fixed inset-0 z-40 flex justify-end bg-black/25"
          onClick={() => setSelected(null)}
        >
          <div
            className="h-full w-full max-w-md overflow-y-auto border-l p-6 shadow-2xl"
            style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-1 flex items-start justify-between">
              <h2 className="text-[17px] font-extrabold">{selected.user.email}</h2>
              <button
                onClick={() => setSelected(null)}
                className="text-[18px]"
                style={{ color: 'var(--text-secondary)' }}
              >
                ✕
              </button>
            </div>
            <p className="mb-5 text-[12px]" style={{ color: 'var(--text-secondary)' }}>
              Joined {formatDate(selected.user.createdAt)} · Last login{' '}
              {formatDate(selected.user.lastLoginAt)}
            </p>

            <div
              className="mb-6 rounded-xl p-4"
              style={{ background: 'var(--prism-primary)', color: '#fff' }}
            >
              <div className="text-[13px] font-bold">Docs Cloud</div>
              <div className="mt-1 text-[12px] opacity-80">
                {formatBytes(selected.user.storageUsedBytes)} of{' '}
                {formatBytes(selected.user.storageLimitBytes)} used
              </div>
            </div>

            <h3 className="mb-2 text-[13px] font-bold">
              Documents ({selected.documents.length})
            </h3>
            <div className="flex flex-col gap-2">
              {selected.documents.length === 0 && (
                <p className="text-[12.5px]" style={{ color: 'var(--text-secondary)' }}>
                  No documents backed up to Docs Cloud.
                </p>
              )}
              {selected.documents.map((doc) => (
                <div
                  key={doc.id}
                  className="rounded-xl border px-3.5 py-2.5"
                  style={{ borderColor: 'var(--divider)' }}
                >
                  <div className="truncate text-[12.5px] font-semibold">{doc.name}</div>
                  <div className="text-[11px]" style={{ color: 'var(--text-secondary)' }}>
                    {formatBytes(doc.sizeBytes)} · {formatDate(doc.modifiedAt)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      <IOSDialog
        open={confirmTarget !== null}
        title={confirmTarget?.isActive ? 'Deactivate this user?' : 'Activate this user?'}
        message={
          confirmTarget?.isActive
            ? `${confirmTarget?.email} will no longer be able to sign in.`
            : `${confirmTarget?.email} will be able to sign in again.`
        }
        onDismiss={() => setConfirmTarget(null)}
        actions={[
          { label: 'Cancel', onClick: () => setConfirmTarget(null) },
          {
            label: confirmTarget?.isActive ? 'Deactivate' : 'Activate',
            destructive: confirmTarget?.isActive,
            isDefault: !confirmTarget?.isActive,
            onClick: () => confirmTarget && toggleActive(confirmTarget),
          },
        ]}
      />
    </div>
  );
}
