import { useEffect } from 'react';
import { useLocation } from 'wouter';
import { setAuthTokenGetter } from '@workspace/api-client-react';

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const [location, setLocation] = useLocation();

  useEffect(() => {
    const token = localStorage.getItem('ec_token');
    if (!token) {
      if (location !== '/login') {
        setLocation('/login');
      }
    }
  }, [location, setLocation]);

  return <>{children}</>;
}

export function initAuth() {
  setAuthTokenGetter(() => localStorage.getItem('ec_token'));
}

export function clearAuth() {
  localStorage.removeItem('ec_token');
  setAuthTokenGetter(() => null);
}

export function useAuth() {
  const [, setLocation] = useLocation();

  const login = (token: string) => {
    localStorage.setItem('ec_token', token);
    setAuthTokenGetter(() => localStorage.getItem('ec_token'));
    setLocation('/dashboard');
  };

  const logout = () => {
    localStorage.removeItem('ec_token');
    setAuthTokenGetter(() => null);
    setLocation('/login');
  };

  return { login, logout };
}
