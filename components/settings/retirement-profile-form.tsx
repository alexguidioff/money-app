'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { useI18n } from '@/lib/i18n-context';
import { COUNTRIES, countryByCode } from '@/lib/data/countries';
import { messaggioErroreFire } from '@/lib/fire-errors';

// Il payload del backend parla con il motore in reali; sul modulo i tassi
// sono percentuali per leggibilita' (4,00 = 4%) e la conversione la fa il
// server. Il modulo non fa arrotondamenti: salva e basta.
export type RetirementProfile = {
  birthYear: number;
  country: string;
  targetRetirementAge: number;
  realReturn: number;
  // Quanto oscillano i rendimenti attorno alla media: entra solo nella
  // simulazione, non nel piano deterministico.
  returnVolatility: number;
  withdrawalRate: number;
  withdrawalTaxRate: number;
  expenseBasis: 'last_year' | 'average' | 'median' | 'custom';
  customAnnualExpenses: number | null;
  leanAnnualExpenses: number | null;
  inflation: number;
  notes: string;
};

const DEFAULTS: RetirementProfile = {
  birthYear: 0,
  country: 'IT',
  targetRetirementAge: 67,
  realReturn: 4,
  returnVolatility: 15,
  withdrawalRate: 4,
  withdrawalTaxRate: 0,
  expenseBasis: 'average',
  customAnnualExpenses: null,
  leanAnnualExpenses: null,
  inflation: 2,
  notes: '',
};

export function RetirementProfileForm({ apiUrl, onSaved }: { apiUrl: string; onSaved?: (profile: RetirementProfile) => void }) {
  const { t, lang } = useI18n();
  const [profile, setProfile] = useState<RetirementProfile>(DEFAULTS);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<{ ok: boolean; message: string } | null>(null);
  const [error, setError] = useState('');

  // Carica il profilo esistente. Se il backend risponde 404 (nessun profilo
  // ancora salvato) si resta coi default: niente errore, niente banner rosso
  // per l'utente che sta aprendo la pagina per la prima volta.
  useEffect(() => {
    const controller = new AbortController();
    fetch(`${apiUrl}/api/fire/profile`, { signal: controller.signal })
      .then((r): Promise<Partial<RetirementProfile> | null> => r.ok ? r.json() as Promise<Partial<RetirementProfile>> : Promise.resolve(null))
      .then((data) => {
        if (data && typeof data === 'object') {
          setProfile((current) => ({ ...current, ...data,
            customAnnualExpenses: data.customAnnualExpenses ?? null,
            notes: data.notes ?? '' }));
        }
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setError(t('fireProfileLoadError'));
      });
    return () => controller.abort();
  }, [apiUrl, t]);

  // Cambiando paese, l'eta' obiettivo proposta e' quella del paese. Solo se
  // l'eta' corrente coincide col default del paese precedente: l'utente che
  // l'ha cambiata a mano non se la vede sovrascrivere.
  function onCountryChange(next: string) {
    setProfile((current) => {
      const previous = countryByCode(current.country);
      const prossimo = countryByCode(next);
      if (previous && prossimo && current.targetRetirementAge === previous.retirementAge) {
        return { ...current, country: next, targetRetirementAge: prossimo.retirementAge };
      }
      return { ...current, country: next };
    });
  }

  // Controlli prima di salvare: messaggi specifici per campo invece di un
  // generico "salvataggio non riuscito" che il backend rimanderebbe.
  function valida(): string {
    const anno = new Date().getFullYear();
    if (!profile.birthYear || profile.birthYear < 1900 || profile.birthYear > anno) {
      return t('fireProfileInvalidBirthYear', { max: anno });
    }
    if (!profile.targetRetirementAge || profile.targetRetirementAge < 18 || profile.targetRetirementAge > 100) {
      return t('fireProfileInvalidRetirementAge');
    }
    if (profile.realReturn < -50 || profile.realReturn > 50) {
      return t('fireProfileInvalidReturn');
    }
    if (profile.returnVolatility < 0 || profile.returnVolatility > 100) {
      return t('fireProfileInvalidVolatility');
    }
    if (profile.withdrawalRate <= 0 || profile.withdrawalRate > 100) {
      return t('fireProfileInvalidWithdrawalRate');
    }
    if (profile.withdrawalTaxRate < 0 || profile.withdrawalTaxRate >= 100) {
      return t('fireProfileInvalidTax');
    }
    if (profile.inflation < 0 || profile.inflation > 50) {
      return t('fireProfileInvalidInflation');
    }
    return '';
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const erroreValidazione = valida();
    if (erroreValidazione) {
      setOutcome({ ok: false, message: erroreValidazione });
      return;
    }
    setBusy(true);
    setOutcome(null);
    setError('');
    try {
      const response = await fetch(`${apiUrl}/api/fire/profile`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(profile),
      });
      if (!response.ok) {
        setOutcome({ ok: false, message: await messaggioErroreFire(response, t, 'fireProfileSaveError') });
        return;
      }
      setOutcome({ ok: true, message: t('fireProfileSaved') });
      onSaved?.(profile);
    } catch {
      setOutcome({ ok: false, message: t('fireProfileSaveError') });
    } finally {
      setBusy(false);
    }
  }

  const country = countryByCode(profile.country);
  const countryName = country ? country[`name${lang.charAt(0).toUpperCase() + lang.slice(1)}` as keyof typeof country] : '';

  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardHeader>
        <CardTitle className="text-[17px]">{t('fireProfileTitle')}</CardTitle>
        <p className="mt-1 text-xs text-[#7b8784]">{t('fireProfileSubtitle')}</p>
      </CardHeader>
      <CardContent>
        <form onSubmit={save} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t('fireProfileBirthYear')} unit={t('fireProfileYears')}>
              <Input type="number" min={1900} max={2100} value={profile.birthYear || ''}
                onChange={(e) => setProfile({ ...profile, birthYear: e.target.value === '' ? 0 : Number(e.target.value) })} className="h-10 bg-white" />
            </Field>
            <Field label={t('fireProfileCountry')}>
              <select value={profile.country} onChange={(e) => onCountryChange(e.target.value)}
                className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm outline-none focus:border-ring">
                {COUNTRIES.map((c) => (
                  <option key={c.code} value={c.code}>{c[`name${lang.charAt(0).toUpperCase() + lang.slice(1)}` as keyof typeof c]}</option>
                ))}
              </select>
              {country && (
                <a href={country.simulatorUrl} target="_blank" rel="noopener noreferrer"
                  className="mt-1 inline-block text-xs text-[#2d7b65] underline">
                  {t('fireProfileCountrySimulator')} ({countryName})
                </a>
              )}
            </Field>
            <Field label={t('fireProfileRetirementAge')} unit={t('fireProfileYears')}>
              <Input type="number" min={18} max={100} value={profile.targetRetirementAge}
                onChange={(e) => setProfile({ ...profile, targetRetirementAge: Number(e.target.value) })} className="h-10 bg-white" />
            </Field>
            <Field label={t('fireProfileRealReturn')} unit={`% (${t('fireProfileReal')})`}>
              <Input type="number" step="0.1" min={-50} max={50} value={profile.realReturn}
                onChange={(e) => setProfile({ ...profile, realReturn: Number(e.target.value) })} className="h-10 bg-white" />
            </Field>
            <Field label={t('fireReturnVolatility')} unit="%">
              <Input type="number" step="0.5" min={0} max={100} value={profile.returnVolatility}
                onChange={(e) => setProfile({ ...profile, returnVolatility: Number(e.target.value) })} className="h-10 bg-white" />
              <span className="block text-[10px] font-normal text-[#87918e]">{t('fireReturnVolatilityHelp')}</span>
            </Field>
            <Field label={t('fireProfileWithdrawalRate')} unit={`% (${t('fireProfileReal')})`}>
              <Input type="number" step="0.1" min={0.1} max={100} value={profile.withdrawalRate}
                onChange={(e) => setProfile({ ...profile, withdrawalRate: Number(e.target.value) })} className="h-10 bg-white" />
            </Field>
            <Field label={t('fireProfileWithdrawalTax')} unit="%" hint={t('fireProfileNominal')}>
              <Input type="number" step="0.1" min={0} max={99.9} value={profile.withdrawalTaxRate || ''}
                onChange={(e) => setProfile({ ...profile, withdrawalTaxRate: e.target.value === '' ? 0 : Number(e.target.value) })} className="h-10 bg-white" />
            </Field>
            <Field label={t('fireProfileExpenseBasis')}>
              <select value={profile.expenseBasis} onChange={(e) => setProfile({ ...profile, expenseBasis: e.target.value as RetirementProfile['expenseBasis'] })}
                className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm outline-none focus:border-ring">
                <option value="last_year">{t('fireProfileExpenseBasisLastYear')}</option>
                <option value="average">{t('fireProfileExpenseBasisAverage')}</option>
                <option value="median">{t('fireProfileExpenseBasisMedian')}</option>
                <option value="custom">{t('fireProfileExpenseBasisCustom')}</option>
              </select>
            </Field>
            {profile.expenseBasis === 'custom' && (
              <Field label={t('fireProfileCustomExpenses')} unit="€">
                <Input type="number" min={0} step="0.01" value={profile.customAnnualExpenses ?? ''}
                  onChange={(e) => setProfile({ ...profile, customAnnualExpenses: e.target.value === '' ? null : Number(e.target.value) })} className="h-10 bg-white" />
              </Field>
            )}
            <Field label={t('fireProfileInflation')} unit="%" hint={t('fireProfileInflationHint')}>
              <Input type="number" step="0.1" min={0} max={50} value={profile.inflation}
                onChange={(e) => setProfile({ ...profile, inflation: Number(e.target.value) })} className="h-10 bg-white" />
            </Field>
            <Field label={t('fireProfileLeanExpenses')} hint={t('fireProfileLeanHint')}>
              <Input type="number" min={0} step="0.01" value={profile.leanAnnualExpenses ?? ''}
                onChange={(e) => setProfile({ ...profile, leanAnnualExpenses: e.target.value === '' ? null : Number(e.target.value) })} className="h-10 bg-white" />
            </Field>
          </div>
          <Field label={t('fireProfileNotes')}>
            <textarea value={profile.notes}
              onChange={(e) => setProfile({ ...profile, notes: e.target.value })}
              className="min-h-[64px] w-full rounded-lg border border-input bg-white px-3 py-2 text-sm outline-none focus:border-ring" />
          </Field>
          {error && <p role="alert" className="rounded-xl border border-[#f4d8ce] bg-[#fce9e3] px-4 py-2 text-sm text-[#bd5e46]">{error}</p>}
          {outcome && (
            <p role="status" className={`rounded-xl border px-4 py-2 text-sm ${outcome.ok ? 'border-[#cfe6dc] bg-[#e5f3ed] text-[#2d7b65]' : 'border-[#f4d8ce] bg-[#fce9e3] text-[#bd5e46]'}`}>
              {outcome.message}
            </p>
          )}
          <div className="flex justify-end">
            <Button type="submit" disabled={busy} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">
              {busy ? '…' : t('fireProfileSave')}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function Field({ label, unit, hint, children }: { label: string; unit?: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1.5 text-xs font-medium text-[#52615d]">
      <span className="flex items-baseline gap-1.5">
        {label}
        {unit && <span className="text-[10px] font-normal text-[#87918e]">({unit})</span>}
        {hint && <span className="text-[10px] font-normal text-[#87918e]">· {hint}</span>}
      </span>
      {children}
    </label>
  );
}
