'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useI18n } from '@/lib/i18n-context';

export type AccountSummary = { id: number; username: string; displayName: string; hasPassword: boolean };

/**
 * Si sceglie chi si è, e si mette la password solo se quell'account ne ha una.
 *
 * Chi non l'ha impostata entra con un clic: gli account non devono diventare
 * un ostacolo per chi non ha bisogno di proteggerli.
 */
export function LoginScreen({ users, onLogin, onCreate }: {
  users: AccountSummary[];
  onLogin: (username: string, password?: string) => Promise<void>;
  onCreate: (name: string) => Promise<void>;
}) {
  const { t } = useI18n();
  const [selected, setSelected] = useState<AccountSummary | null>(users.length === 1 ? users[0] : null);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // Sulla prima installazione non c'e' nessuno: il modulo di creazione e'
  // l'unica cosa che ha senso mostrare, quindi si apre da solo.
  const [creazione, setCreazione] = useState(users.length === 0);
  const [nome, setNome] = useState('');

  async function crea(event: React.FormEvent) {
    event.preventDefault();
    if (!nome.trim()) return;
    setBusy(true);
    setError('');
    try {
      await onCreate(nome.trim());
      setNome('');
      setCreazione(false);
    } catch (errore) {
      setError(errore instanceof Error ? errore.message : t('cannotSaveGeneric'));
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!selected) return;
    setBusy(true);
    setError('');
    try {
      await onLogin(selected.username, selected.hasPassword ? password : undefined);
    } catch (error) {
      setError(t(error instanceof Error && error.message === 'loginTooManyAttempts' ? 'loginTooManyAttempts' : 'loginFailed'));
      setPassword('');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#f4f5f1] px-4">
      <div className="w-full max-w-sm rounded-2xl border border-black/6 bg-white p-6 shadow-sm">
        <h1 className="text-lg font-semibold text-[#173b33]">{t('appName')}</h1>
        <p className="mt-1 text-xs text-[#7b8784]">
          {users.length === 0 ? t('loginFirstAccount') : t('loginSubtitle')}
        </p>

        <div className="mt-5 space-y-2">
          {users.map((user) => (
            <button
              key={user.id}
              type="button"
              onClick={() => { setSelected(user); setPassword(''); setError(''); }}
              className={`flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition ${
                selected?.id === user.id
                  ? 'border-[var(--money-primary)] bg-[var(--money-primary)]/8'
                  : 'border-black/8 hover:bg-black/[0.02]'
              }`}
            >
              <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-[var(--money-primary)] text-sm font-semibold text-white">
                {user.displayName.slice(0, 1).toUpperCase()}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-[#173b33]">{user.displayName}</span>
                <span className="block text-[11px] text-[#87918e]">
                  {user.hasPassword ? t('loginNeedsPassword') : t('loginNoPassword')}
                </span>
              </span>
            </button>
          ))}
        </div>

        <form onSubmit={submit} className="mt-4 space-y-3">
          {selected?.hasPassword && (
            <Input
              type="password"
              autoFocus
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={t('loginPassword')}
              className="h-10 bg-white"
            />
          )}
          {error && <p className="rounded-lg bg-[#fff6f3] px-3 py-2 text-xs text-[#a05f4e]">{error}</p>}
          {users.length > 0 && (
            <Button
              type="submit"
              disabled={!selected || busy || (selected.hasPassword && !password)}
              className="h-10 w-full bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]"
            >
              {busy ? t('loginBusy') : t('loginEnter')}
            </Button>
          )}
        </form>

        {creazione ? (
          <form onSubmit={crea} className="mt-4 space-y-2.5 border-t border-black/6 pt-4">
            <p className="text-sm font-medium text-[#173b33]">{t('addPerson')}</p>
            <p className="text-xs leading-5 text-[#7b8784]">{t('addPersonHint')}</p>
            <Input value={nome} onChange={(event) => setNome(event.target.value)}
                   placeholder={t('personName')} className="h-10 bg-white" autoFocus={users.length === 0} />
            <div className="flex gap-2">
              <Button type="submit" disabled={busy || !nome.trim()}
                      className="h-10 flex-1 bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">
                {t('addPerson')}
              </Button>
              {users.length > 0 && (
                <Button type="button" variant="outline" className="h-10" onClick={() => { setCreazione(false); setError(''); }}>
                  {t('cancel')}
                </Button>
              )}
            </div>
          </form>
        ) : (
          <button type="button" onClick={() => { setCreazione(true); setError(''); }}
                  className="mt-4 w-full text-center text-xs font-medium text-[#397867] hover:underline">
            {t('addPerson')}
          </button>
        )}
      </div>
    </div>
  );
}
