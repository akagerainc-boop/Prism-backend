import { useEffect, useState, type ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import { IOSDialog } from './IOSDialog';

const NAV_ITEMS = [
  { to: '/users', label: 'Users', icon: '👤' },
  { to: '/devices', label: 'Devices', icon: '📱' },
  { to: '/force-update', label: 'Force Update', icon: '⬆️' },
  { to: '/notifications', label: 'Notifications', icon: '🔔' },
];

function useTheme() {
  const [theme, setTheme] = useState<'light' | 'dark'>(
    () => (localStorage.getItem('prism_admin_theme') as 'light' | 'dark' | null) ?? 'light',
  );

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('prism_admin_theme', theme);
  }, [theme]);

  return { theme, toggle: () => setTheme((t) => (t === 'light' ? 'dark' : 'light')) };
}

export function Layout({ children }: { children: ReactNode }) {
  const { displayName, logout } = useAuth();
  const { theme, toggle } = useTheme();
  const [confirmLogout, setConfirmLogout] = useState(false);

  return (
    <div className="flex min-h-screen" style={{ background: 'var(--bg)' }}>
      <aside
        className="flex w-64 shrink-0 flex-col border-r px-4 py-6"
        style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
      >
        <div className="mb-8 flex items-center gap-2.5 px-2">
          <div
            className="flex h-10 w-10 items-center justify-center rounded-xl text-lg font-black text-white"
            style={{ background: 'var(--prism-primary)' }}
          >
            P
          </div>
          <div>
            <div className="text-[15px] font-extrabold leading-tight">Prism Scanner</div>
            <div className="text-[11px] leading-tight" style={{ color: 'var(--text-secondary)' }}>
              Admin
            </div>
          </div>
        </div>

        <nav className="flex flex-1 flex-col gap-1">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }: { isActive: boolean }) =>
                `flex items-center gap-3 rounded-xl px-3 py-2.5 text-[13.5px] font-semibold transition-colors ${
                  isActive ? 'text-white' : 'hover:bg-black/5 dark:hover:bg-white/5'
                }`
              }
              style={({ isActive }: { isActive: boolean }) => ({
                background: isActive ? 'var(--prism-primary)' : 'transparent',
                color: isActive ? '#fff' : 'var(--text-primary)',
              })}
            >
              <span>{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="mt-auto flex flex-col gap-2 border-t pt-4" style={{ borderColor: 'var(--divider)' }}>
          <button
            onClick={toggle}
            className="flex items-center gap-2 rounded-xl px-3 py-2 text-left text-[13px] font-semibold hover:bg-black/5 dark:hover:bg-white/5"
          >
            {theme === 'light' ? '🌙 Dark theme' : '☀️ Light theme'}
          </button>
          <div className="px-3 text-[12px]" style={{ color: 'var(--text-secondary)' }}>
            {displayName ?? 'Admin'}
          </div>
          <button
            onClick={() => setConfirmLogout(true)}
            className="rounded-xl px-3 py-2 text-left text-[13px] font-semibold text-[#c53636] hover:bg-black/5 dark:hover:bg-white/5"
          >
            Log out
          </button>
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto px-10 py-8">{children}</main>

      <IOSDialog
        open={confirmLogout}
        title="Log out?"
        message="You'll need to sign in again to access the admin dashboard."
        onDismiss={() => setConfirmLogout(false)}
        actions={[
          { label: 'Cancel', onClick: () => setConfirmLogout(false) },
          { label: 'Log out', destructive: true, onClick: logout },
        ]}
      />
    </div>
  );
}
