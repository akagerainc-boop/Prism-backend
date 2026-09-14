import { useState, type FormEvent } from 'react';
import { useAuth } from '../lib/auth';
import { ApiError } from '../lib/api';

export function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(email.trim(), password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      className="flex min-h-screen items-center justify-center px-6"
      style={{ background: 'var(--bg)' }}
    >
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm rounded-[22px] border p-8 shadow-sm"
        style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
      >
        <div className="mb-6 flex flex-col items-center">
          <div
            className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl text-2xl font-black text-white"
            style={{ background: 'var(--prism-primary)' }}
          >
            P
          </div>
          <h1 className="text-[18px] font-extrabold">Docs Scanner Admin</h1>
          <p className="mt-1 text-[12.5px]" style={{ color: 'var(--text-secondary)' }}>
            Sign in to manage the app
          </p>
        </div>

        {error && (
          <div className="mb-4 rounded-xl bg-[#c53636]/10 px-3 py-2.5 text-[12.5px] font-medium text-[#c53636]">
            {error}
          </div>
        )}

        <label className="mb-3 block">
          <span className="mb-1.5 block text-[12px] font-semibold" style={{ color: 'var(--text-secondary)' }}>
            Email
          </span>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-xl border px-3.5 py-2.5 text-[14px] outline-none focus:border-[var(--prism-primary)]"
            style={{ borderColor: 'var(--border)', background: 'var(--bg)', color: 'var(--text-primary)' }}
          />
        </label>

        <label className="mb-5 block">
          <span className="mb-1.5 block text-[12px] font-semibold" style={{ color: 'var(--text-secondary)' }}>
            Password
          </span>
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-xl border px-3.5 py-2.5 text-[14px] outline-none focus:border-[var(--prism-primary)]"
            style={{ borderColor: 'var(--border)', background: 'var(--bg)', color: 'var(--text-primary)' }}
          />
        </label>

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-xl py-3 text-[14px] font-bold text-white transition-opacity disabled:opacity-60"
          style={{ background: 'var(--prism-primary)' }}
        >
          {loading ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}
