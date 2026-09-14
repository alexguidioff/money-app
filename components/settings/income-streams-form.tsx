'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { useI18n } from '@/lib/i18n-context';
import { COUNTRIES } from '@/lib/data/countries';
import { messaggioErroreFire } from '@/lib/fire-errors';
import { incomeStreamPayload } from '@/lib/payloads';

// Lo stream e' la metafora centrale del piano: un futuro reddito con eta' di
// inizio, tipo (rendita o capitale), importo che l'utente prende dal
// simulatore del suo ente. Qui si raccolgono, si modificano, si cancellano.

export type IncomeStream = {
  id: number;
  name: string;
  kind: 'annuity' | 'capital';
  amount: number;
  startAge: number;
  indexed: boolean;
  country: string | null;
  amountIfStoppingNow: number | null;
  notes: string;
};

const EMPTY: Omit<IncomeStream, 'id'> = {
  name: '',
  kind: 'annuity',
  amount: 0,
  startAge: 67,
  indexed: true,
  country: null,
  amountIfStoppingNow: null,
  notes: '',
};

export function IncomeStreamsForm({ apiUrl }: { apiUrl: string }) {
  const { t, lang, formatEuro, formatDate } = useI18n();
  const [streams, setStreams] = useState<IncomeStream[]>([]);
  const [editing, setEditing] = useState<IncomeStream | null>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [outcome, setOutcome] = useState<{ ok: boolean; message: string } | null>(null);

  // Carica i flussi. Stesso pattern del profilo: niente banner rosso se il
  // primo fetch e' vuoto, l'utente sta solo aprendo la pagina.
  useEffect(() => {
    const controller = new AbortController();
    fetch(`${apiUrl}/api/fire/streams`, { signal: controller.signal })
      .then((r) => r.ok ? r.json() as Promise<{ streams: IncomeStream[] }> : Promise.reject(new Error('streams')))
      .then((data) => setStreams(data.streams ?? []))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setError(t('fireStreamsLoadError'));
      });
    return () => controller.abort();
  }, [apiUrl, t]);

  // Salva (crea o aggiorna). Il backend restituisce la riga con l'id;
  // aggiorniamo lo stato in place. Niente optimistic update per non rischiare
  // un duplicato visibile se il POST fallisce.
  async function save(values: Omit<IncomeStream, 'id'>, id?: number) {
    setBusy(true);
    setError('');
    setOutcome(null);
    try {
      const url = id ? `${apiUrl}/api/fire/streams/${id}` : `${apiUrl}/api/fire/streams`;
      const response = await fetch(url, {
        method: id ? 'PATCH' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(incomeStreamPayload(values)),
      });
      if (!response.ok) {
        setOutcome({ ok: false, message: await messaggioErroreFire(response, t, 'fireStreamsSaveError') });
        return;
      }
      const saved = await response.json() as IncomeStream;
      setStreams((current) => id
        ? current.map((s) => s.id === id ? saved : s)
        : [...current, saved].sort((a, b) => a.startAge - b.startAge));
      setCreating(false);
      setEditing(null);
      setOutcome({ ok: true, message: t('fireStreamsSaved') });
    } catch {
      setOutcome({ ok: false, message: t('fireStreamsSaveError') });
    } finally {
      setBusy(false);
    }
  }

  // Cancella con il confirm del browser, come `handleNoteDelete` nel resto
  // dell'app. Niente dialog shadcn qui per non pesare il bundle.
  async function cancella(stream: IncomeStream) {
    if (typeof window !== 'undefined' && !window.confirm(t('fireStreamsDeleteConfirm'))) return;
    setBusy(true);
    try {
      const response = await fetch(`${apiUrl}/api/fire/streams/${stream.id}`, { method: 'DELETE' });
      if (!response.ok) throw new Error();
      setStreams((current) => current.filter((s) => s.id !== stream.id));
    } catch {
      setError(t('fireStreamsDeleteError'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardHeader>
        <CardTitle className="text-[17px]">{t('fireStreamsTitle')}</CardTitle>
        <p className="mt-1 text-xs text-[#7b8784]">{t('fireStreamsSubtitle')}</p>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <p role="alert" className="rounded-xl border border-[#f4d8ce] bg-[#fce9e3] px-4 py-2 text-sm text-[#bd5e46]">{error}</p>}
        {outcome && (
          <p role="status" className={`rounded-xl border px-4 py-2 text-sm ${outcome.ok ? 'border-[#cfe6dc] bg-[#e5f3ed] text-[#2d7b65]' : 'border-[#f4d8ce] bg-[#fce9e3] text-[#bd5e46]'}`}>
            {outcome.message}
          </p>
        )}
        {streams.length === 0 ? (
          <p className="rounded-xl border border-dashed border-black/10 bg-[#fafaf8] px-4 py-6 text-center text-sm text-[#7b8784]">{t('fireStreamsEmpty')}</p>
        ) : (
          <ul className="divide-y divide-black/5 rounded-xl border border-black/6 bg-white">
            {streams.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{s.name}</p>
                  <p className="mt-0.5 text-xs text-[#7b8784]">
                    {s.kind === 'annuity' ? t('fireStreamsKindAnnuity') : t('fireStreamsKindCapital')} ·
                    {' '}{formatEuro(s.amount)} ·
                    {' '}{t('fireProfileYears')}: {s.startAge}
                    {s.indexed ? ` · ${t('fireStreamsIndexed')}` : ''}
                    {s.country ? ` · ${s.country}` : ''}
                  </p>
                </div>
                <Button size="sm" variant="outline" disabled={busy} onClick={() => { setEditing(s); setCreating(false); }}>
                  {t('fireStreamsEdit')}
                </Button>
                <Button size="sm" variant="outline" disabled={busy} onClick={() => void cancella(s)} className="text-[#bd5e46]">
                  {t('fireStreamsDelete')}
                </Button>
              </li>
            ))}
          </ul>
        )}
        {!creating && !editing && (
          <div className="flex justify-end">
            <Button onClick={() => setCreating(true)} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">
              {t('fireStreamsAdd')}
            </Button>
          </div>
        )}
        {creating && (
          <StreamEditor t={t} lang={lang} initial={EMPTY} busy={busy}
            onCancel={() => setCreating(false)} onSave={(values) => void save(values)} />
        )}
        {editing && (
          <StreamEditor t={t} lang={lang} initial={editing} busy={busy}
            onCancel={() => setEditing(null)} onSave={(values) => void save(values, editing.id)} />
        )}
      </CardContent>
    </Card>
  );
}

function StreamEditor({ t, lang, initial, busy, onCancel, onSave }: {
  t: ReturnType<typeof useI18n>['t']; lang: string; initial: Omit<IncomeStream, 'id'> | IncomeStream;
  busy: boolean; onCancel: () => void; onSave: (values: Omit<IncomeStream, 'id'>) => void;
}) {
  const [values, setValues] = useState<Omit<IncomeStream, 'id'>>({ ...EMPTY, ...initial });
  return (
    <form onSubmit={(e) => { e.preventDefault(); onSave(values); }} className="space-y-4 rounded-xl border border-black/6 bg-[#fafaf8] p-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block space-y-1.5 text-xs font-medium text-[#52615d]">
          {t('fireStreamsName')}
          <Input required value={values.name} placeholder={t('fireStreamsNamePlaceholder')}
            onChange={(e) => setValues({ ...values, name: e.target.value })} className="h-10 bg-white" />
        </label>
        <label className="block space-y-1.5 text-xs font-medium text-[#52615d]">
          {t('fireStreamsKind')}
          <select value={values.kind} onChange={(e) => setValues({ ...values, kind: e.target.value as IncomeStream['kind'] })}
            className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm outline-none focus:border-ring">
            <option value="annuity">{t('fireStreamsKindAnnuity')}</option>
            <option value="capital">{t('fireStreamsKindCapital')}</option>
          </select>
        </label>
        <label className="block space-y-1.5 text-xs font-medium text-[#52615d]">
          {t('fireStreamsAmount')} (€)
          <Input type="number" min={0} step="0.01" value={values.amount}
            onChange={(e) => setValues({ ...values, amount: Number(e.target.value) })} className="h-10 bg-white" />
        </label>
        <label className="block space-y-1.5 text-xs font-medium text-[#52615d]">
          {t('fireStreamsStartAge')} ({t('fireProfileYears')})
          <Input type="number" min={18} max={100} value={values.startAge}
            onChange={(e) => setValues({ ...values, startAge: Number(e.target.value) })} className="h-10 bg-white" />
        </label>
        <label className="block space-y-1.5 text-xs font-medium text-[#52615d]">
          {t('fireStreamsCountry')}
          <select value={values.country ?? ''} onChange={(e) => setValues({ ...values, country: e.target.value || null })}
            className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm outline-none focus:border-ring">
            <option value="">—</option>
            {COUNTRIES.map((c) => (
              <option key={c.code} value={c.code}>{c[`name${lang.charAt(0).toUpperCase() + lang.slice(1)}` as keyof typeof c]}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm text-[#52615d]">
          <input type="checkbox" checked={values.indexed} onChange={(e) => setValues({ ...values, indexed: e.target.checked })}
            className="size-4 accent-[var(--money-primary)]" />
          {t('fireStreamsIndexed')}
        </label>
        <label className="block space-y-1.5 text-xs font-medium text-[#52615d] sm:col-span-2">
          {t('fireStreamsEarlyPayoutAmount')}
          <Input type="number" min={0} step="0.01" value={values.amountIfStoppingNow ?? ''}
            onChange={(e) => setValues({ ...values, amountIfStoppingNow: e.target.value === '' ? null : Number(e.target.value) })} className="h-10 bg-white" />
        </label>
        <label className="block space-y-1.5 text-xs font-medium text-[#52615d] sm:col-span-2">
          {t('fireStreamsNotes')}
          <textarea value={values.notes} onChange={(e) => setValues({ ...values, notes: e.target.value })}
            className="min-h-[48px] w-full rounded-lg border border-input bg-white px-3 py-2 text-sm outline-none focus:border-ring" />
        </label>
      </div>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onCancel} disabled={busy}>{t('cancel')}</Button>
        <Button type="submit" disabled={busy || !values.name} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">
          {t('fireStreamsSave')}
        </Button>
      </div>
    </form>
  );
}
