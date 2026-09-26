import React, { useState } from 'react';
import { LogIn, UserPlus, Lock, User, AlertCircle, Shield } from 'lucide-react';


interface AuthModalProps {
  isOpen: boolean;
  onLoginSuccess: (user: { user_id: number; username: string }) => void;
  onRegisterSuccess: (user: { user_id: number; username: string; api_key: string }) => void;
  onClose?: () => void;
}

export const AuthModal: React.FC<AuthModalProps> = ({
  isOpen,
  onLoginSuccess,
  onRegisterSuccess,
}) => {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    const cleanUsername = username.trim().toLowerCase();
    if (!cleanUsername) {
      setError('Please enter a username.');
      setLoading(false);
      return;
    }

    if (mode === 'register' && cleanUsername === 'demo') {
      setError("Username 'demo' is reserved for public fixtures.");
      setLoading(false);
      return;
    }

    try {
      const endpoint = mode === 'register' ? '/api/auth/register' : '/api/auth/login';
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: cleanUsername, password }),
      });

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        setError(data.detail || 'Authentication failed. Please check your credentials.');
        setLoading(false);
        return;
      }

      if (mode === 'register') {
        onRegisterSuccess({
          user_id: data.user_id,
          username: data.username,
          api_key: data.api_key,
        });
      } else {
        onLoginSuccess({
          user_id: data.user_id,
          username: data.username,
        });
      }
    } catch (err: any) {
      setError(err?.message || 'Network error connecting to auth service.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-950/70 backdrop-blur-xs flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl w-full max-w-md shadow-2xl border border-slate-200 overflow-hidden text-slate-800">
        <div className="bg-[#14121F] p-6 text-white text-center border-b border-[#2B2644]">
          <div className="w-12 h-12 mx-auto rounded-xl bg-gradient-to-tr from-[#6B46C1] to-[#9F7AEA] flex items-center justify-center text-white shadow-lg mb-3">
            <span className="font-mono font-bold text-xl leading-none">⑂</span>
          </div>
          <h2 className="text-xl font-bold tracking-tight">OpenEval Studio</h2>
          <p className="text-xs text-slate-300 mt-1">
            {mode === 'login' ? 'Sign in to access your private studio' : 'Create an account to start monitoring agents'}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {error && (
            <div className="p-3 rounded-lg bg-red-50 border border-red-200 text-xs text-red-700 flex items-start gap-2">
              <AlertCircle className="w-4 h-4 text-red-500 shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}

          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-slate-700 block">Username</label>
            <div className="relative">
              <User className="w-4 h-4 text-slate-400 absolute left-3 top-2.5" />
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="e.g. alice"
                className="w-full pl-9 pr-3 py-2 rounded-lg border border-slate-300 text-xs font-medium focus:outline-hidden focus:ring-2 focus:ring-purple-500"
                required
                autoFocus
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-slate-700 block">Password</label>
            <div className="relative">
              <Lock className="w-4 h-4 text-slate-400 absolute left-3 top-2.5" />
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="w-full pl-9 pr-3 py-2 rounded-lg border border-slate-300 text-xs font-medium focus:outline-hidden focus:ring-2 focus:ring-purple-500"
                required
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 rounded-lg bg-purple-600 hover:bg-purple-700 text-white font-semibold text-xs transition-colors shadow-sm flex items-center justify-center gap-2 cursor-pointer disabled:opacity-50"
          >
            {loading ? (
              <span>Connecting...</span>
            ) : mode === 'login' ? (
              <>
                <LogIn className="w-4 h-4" />
                <span>Sign In</span>
              </>
            ) : (
              <>
                <UserPlus className="w-4 h-4" />
                <span>Create Account</span>
              </>
            )}
          </button>

          <div className="pt-2 text-center text-xs text-slate-500 border-t border-slate-100 flex items-center justify-between">
            <span>
              {mode === 'login' ? "Don't have an account?" : 'Already registered?'}
            </span>
            <button
              type="button"
              onClick={() => {
                setMode(mode === 'login' ? 'register' : 'login');
                setError(null);
              }}
              className="text-purple-600 font-semibold hover:underline cursor-pointer"
            >
              {mode === 'login' ? 'Create Account' : 'Sign In'}
            </button>
          </div>

          <div className="pt-2 text-center">
            <a
              href="/demo"
              className="text-[11px] text-slate-400 hover:text-slate-600 inline-flex items-center gap-1"
            >
              <Shield className="w-3 h-3 text-slate-400" />
              <span>Or explore public demo mode (/demo)</span>
            </a>
          </div>
        </form>
      </div>
    </div>
  );
};
