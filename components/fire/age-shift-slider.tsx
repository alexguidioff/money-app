'use client';

import { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useI18n } from '@/lib/i18n-context';

// E se le pensioni arrivano prima o dopo? Le eta' pensionabili europee si
// muovono con l'aspettativa di vita, e a trent'anni di distanza quella di
// oggi e' un'ipotesi. Il server calcola in una volta il piano per ogni
// spostamento da -5 a +5: il cursore sceglie fra risultati gia' pronti.
// E' un "e se", non modifica i flussi salvati.

export type PensionShiftResult = { shift: number; capitalNeeded: number | null; reachedAtAge: number | null; reachedInYear: number | null };
export type PensionShiftData = { hasStreams: boolean; results: PensionShiftResult[] };

export function AgeShiftSlider({ apiUrl }: { apiUrl: string }) {
  const { t, formatEuro } = useI18n();
  const [dati, setDati] = useState<PensionShiftData | null>(null);
  const [shift, setShift] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${apiUrl}/api/fire/pension-shift`, { signal: controller.signal })
      .then((r) => r.ok ? r.json() as Promise<PensionShiftData> : null)
      .then(setDati)
      // Card accessoria: se non carica non si mostra, la pagina resta intera.
      .catch(() => undefined);
    return () => controller.abort();
  }, [apiUrl]);

  if (!dati) return null;
  const base = dati.results.find((r) => r.shift === 0);
  const scelto = dati.results.find((r) => r.shift === shift);
  const etichetta = shift === 0 ? t('fireAgeShiftNone')
    : shift > 0 ? t('fireAgeShiftLater', { years: shift }) : t('fireAgeShiftEarlier', { years: -shift });
  const delta = scelto?.capitalNeeded != null && base?.capitalNeeded != null ? scelto.capitalNeeded - base.capitalNeeded : null;

  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardHeader>
        <CardTitle className="text-[17px]">{t('fireAgeShiftTitle')}</CardTitle>
        <p className="mt-1 text-xs text-[#7b8784]">{t('fireAgeShiftSubtitle')}</p>
      </CardHeader>
      <CardContent className="space-y-4">
        {!dati.hasStreams ? <p className="text-sm text-[#7b8784]">{t('fireAgeShiftNoStreams')}</p> : <>
          <p className="text-sm font-semibold text-[#3a4a46]">{etichetta}</p>
          <input type="range" min={-5} max={5} step={1} value={shift}
            onChange={(e) => setShift(Number(e.target.value))} aria-label={t('fireAgeShiftTitle')} aria-valuetext={etichetta}
            className="h-2 w-full cursor-pointer appearance-none rounded-full bg-[#e7eae6] accent-[var(--money-primary)]" />
          <div className="flex justify-between text-[11px] text-[#87918e]"><span>-5</span><span>0</span><span>+5</span></div>
          {scelto && <div className="rounded-xl border border-black/6 bg-[#fafaf8] p-3 text-sm">
            <p className="text-xs text-[#7b8784]">{t('fireCapitalNeeded')}</p>
            <p className="text-lg font-semibold tabular-nums text-[#3a4a46]">{scelto.capitalNeeded != null ? formatEuro(scelto.capitalNeeded) : '—'}</p>
            {delta !== null && shift !== 0 && (
              <p className={`text-xs tabular-nums ${delta > 0 ? 'text-[#bd5e46]' : 'text-[#2d7b65]'}`}>
                {t('fireAgeShiftDelta', { amount: `${delta > 0 ? '+' : ''}${formatEuro(delta)}` })}
              </p>
            )}
            <p className="mt-2 text-[#3a4a46]">
              {scelto.reachedAtAge !== null && scelto.reachedInYear !== null
                ? t('fireReachedAt', { age: scelto.reachedAtAge, year: scelto.reachedInYear }) : t('fireNotReached')}
            </p>
          </div>}
        </>}
      </CardContent>
    </Card>
  );
}
