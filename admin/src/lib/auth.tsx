import { createContext, useContext, useState, type ReactNode } from 'react';
import { api, clearToken, getToken, setToken } from './api';

interface AdminLoginResponse {
  message: string;
  token: string;
  displayName: string | null;
}

interface AuthContextValue {
  isAuthenticated: boolean;
  displayName: string | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(() => Boolean(getToken()));
  const [displayName, setDisplayName] = useState<string | null>(
    () => localStorage.getItem('prism_admin_name'),
  );

  async function login(email: string, password: string) {
    const result = await api.post<AdminLoginResponse>(
      '/admin/auth/login',
      { email, password },
      { auth: false },
    );
    setToken(result.token);
    if (result.displayName) localStorage.setItem('prism_admin_name', result.displayName);
    setDisplayName(result.displayName);
    setIsAuthenticated(true);
  }

  function logout() {
    clearToken();
    localStorage.removeItem('prism_admin_name');
    setIsAuthenticated(false);
    setDisplayName(null);
  }

  return (
    <AuthContext.Provider value={{ isAuthenticated, displayName, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
