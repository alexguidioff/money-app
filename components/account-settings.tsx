'use client';

import { useState } from 'react';
import { downloadFile } from '@/lib/download';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { useI18n } from '@/lib/i18n-context';

export type AccountState = { id: number; displayName: string; hasPassword: boolean; sharesTotals: boolean };

/**
 * Il proprio account: la password e cosa si lascia vedere agli altri.
 *
 * La condivisione e' spenta finche' non la si accende, e riguarda solo i
 * totali. Il dettaglio non e' condivisibile nemmeno volendo.
 */
export function AccountSettings({ apiUrl, account, onChanged }: {
  apiUrl: string;
  account: AccountState;
  onChanged: () => Promise<void>;
}) {
  const { t } = useI18n();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<{ ok: boolean; message: string } | null>(null);
  // Separato dall'esito della password: condiviso, l'errore della casella
  // compariva sotto il form della password, lontano da cio' che l'aveva causato.
  const [erroreCondivisione, setErroreCondivisione] = useState('');
  const [cancellazione, setCancellazione] = useState(false);
  const [conferma, setConferma] = useState('');
  const [erroreCancellazione, setErroreCancellazione] = useState('');
  async function savePassword(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setOutcome(null);
    try {
      const response = await fetch(`${apiUrl}/api/auth/password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current: current || null, new: next }),
      });
      const payload = await response.json().catch(() => null) as { detail?: string } | null;
      if (!response.ok) {
        const messaggio = payload?.detail === 'password_too_short' ? t('passwordTooShort')
          : payload?.detail === 'wrong_current_password' ? t('passwordWrong')
          : t('cannotSaveGeneric');
        throw new Error(messaggio);
      }
      setOutcome({ ok: true, message: t('passwordSaved') });
      setCurrent('');
      setNext('');
      await onChanged();
    } catch (error) {
      setOutcome({ ok: false, message: error instanceof Error ? error.message : t('cannotSaveGeneric') });
    } finally {
      setBusy(false);
    }
  }

  async function toggleSharing(value: boolean) {
    setBusy(true);
    setErroreCondivisione('');
    try {
      const response = await fetch(`${apiUrl}/api/users/${account.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shares_totals: value }),
      });
      // Il server risponde con codici (`not_your_account`): non sono frasi da
      // mostrare.
      if (!response.ok) throw new Error(t('cannotSaveGeneric'));
      await onChanged();
    } catch {
      setErroreCondivisione(t('cannotSaveGeneric'));
    } finally {
      setBusy(false);
    }
  }

  /**
   * Cancella l'account, dopo aver messo i dati in mano a chi se ne va.
   *
   * Il file parte prima della cancellazione apposta: se il download fallisce
   * non si cancella niente, invece di distruggere dati che nessuno ha in mano.
   * Il server ne tiene comunque una copia accanto ai backup.
   */
  async function cancellaAccount() {
    setBusy(true);
    setErroreCancellazione('');
    try {
      await downloadFile(`${apiUrl}/api/export/data`, `money-${account.displayName.toLowerCase().replace(/\s+/g, '-')}.xlsx`, t);

      const risposta = await fetch(
        `${apiUrl}/api/users/${account.id}?confirm=${encodeURIComponent(conferma)}`,
        { method: 'DELETE' },
      );
      const esito = await risposta.json().catch(() => null) as { detail?: string } | null;
      if (!risposta.ok) {
        throw new Error(esito?.detail === 'confirm_mismatch'
          ? t('deleteAccountMismatch') : t('cannotSaveGeneric'));
      }
      window.location.reload();
    } catch (errore) {
      setErroreCancellazione(errore instanceof Error ? errore.message : t('cannotSaveGeneric'));
      setBusy(false);
    }
  }

  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardHeader>
        <CardTitle className="text-[17px]">{t('account')}</CardTitle>
        <p className="text-xs leading-5 text-[#7b8784]">{t('accountSubtitle')}</p>
      </CardHeader>
      <CardContent className="space-y-5">
        <label className="flex items-start gap-3">
          <input
            type="checkbox"
            checked={account.sharesTotals}
            disabled={busy}
            onChange={(event) => void toggleSharing(event.target.checked)}
            className="mt-0.5 size-4 accent-[var(--money-primary)]"
          />
          <span>
            <span className="block text-sm font-medium text-[#173b33]">{t('shareTotals')}</span>
            <span className="mt-0.5 block text-xs leading-5 text-[#7b8784]">{t('shareTotalsHint')}</span>
            {erroreCondivisione && <span role="alert" className="mt-1 block text-xs text-[#a65b49]">{erroreCondivisione}</span>}
          </span>
        </label>

        <form onSubmit={savePassword} className="space-y-2.5 border-t border-black/6 pt-4">
          <p className="text-sm font-medium text-[#173b33]">
            {account.hasPassword ? t('changePassword') : t('setPassword')}
          </p>
          {account.hasPassword && (
            <Input type="password" value={current} onChange={(event) => setCurrent(event.target.value)}
                   placeholder={t('currentPassword')} className="h-10 bg-white" autoComplete="current-password" />
          )}
          <Input type="password" value={next} onChange={(event) => setNext(event.target.value)}
                 placeholder={t('newPassword')} className="h-10 bg-white" autoComplete="new-password" />
          <Button type="submit" disabled={busy || next.length < 6}
                  className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">
            {account.hasPassword ? t('changePassword') : t('setPassword')}
          </Button>
          {outcome && (
            <p className={`text-xs ${outcome.ok ? 'text-[#2d7b65]' : 'text-[#a65b49]'}`}>{outcome.message}</p>
          )}
        </form>

        <div className="border-t border-black/6 pt-4">
          {cancellazione ? (
            <div className="space-y-2.5">
              <p className="text-sm font-medium text-[#a65b49]">{t('deleteAccount')}</p>
              <p className="text-xs leading-5 text-[#7b8784]">{t('deleteAccountHint')}</p>
              <Input value={conferma} onChange={(event) => setConferma(event.target.value)}
                     placeholder={t('deleteAccountConfirm', { name: account.displayName })}
                     className="h-10 bg-white" />
              <div className="flex flex-wrap gap-2">
                <Button type="button" disabled={busy || !conferma.trim()}
                        onClick={() => void cancellaAccount()}
                        className="bg-[#a65b49] text-white hover:bg-[#8f4d3d]">
                  {busy ? t('deleteAccountBusy') : t('deleteAccount')}
                </Button>
                <Button type="button" variant="outline" disabled={busy}
                        onClick={() => { setCancellazione(false); setConferma(''); setErroreCancellazione(''); }}>
                  {t('cancel')}
                </Button>
              </div>
              {erroreCancellazione && <p className="text-xs text-[#a65b49]">{erroreCancellazione}</p>}
            </div>
          ) : (
            <button type="button" onClick={() => setCancellazione(true)}
                    className="text-xs font-medium text-[#a65b49] hover:underline">
              {t('deleteAccount')}
            </button>
          )}
        </div>

      </CardContent>
    </Card>
  );
}
