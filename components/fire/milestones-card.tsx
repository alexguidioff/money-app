'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useI18n } from '@/lib/i18n-context';

// I traguardi che il motore espone: coast, solo-ponte, lean, FI. Ognuno ha
// un capitale necessario e un flag "raggiunto" che vale **oggi**: il motore
// non da' un'eta' per traguardo, e quella dell'FI non va prestata agli altri.
// Anno e anni mancanti stanno gia' in testa alla pagina.

type MilestoneValue = {
  capitalNeeded: number | null;
  reached: boolean | null;
};

export type MilestonesPayload = {
  coast?: MilestoneValue;
  bridge?: MilestoneValue;
  lean?: MilestoneValue;
  fi?: MilestoneValue;
};

export function MilestonesCard({ milestones, capital, leanNote }: {
  milestones: MilestonesPayload; capital: number;
  // Sostituisce il "mancano" di Lean quando le spese lean non sono piu' basse
  // di quelle di riferimento e il traguardo coincide con FI.
  leanNote?: string;
}) {
  const { t, formatEuro } = useI18n();
  const items: Array<{ key: keyof MilestonesPayload; label: string }> = [
    { key: 'coast', label: t('fireMilestoneCoast') },
    { key: 'bridge', label: t('fireMilestoneBridge') },
    { key: 'lean', label: t('fireMilestoneLean') },
    { key: 'fi', label: t('fireMilestoneFI') },
  ];
  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardHeader>
        <CardTitle className="text-[17px]">{t('fireMilestonesTitle')}</CardTitle>
        <p className="mt-1 text-xs text-[#7b8784]">{t('fireMilestonesSubtitle')}</p>
      </CardHeader>
      <CardContent>
        <ul className="grid gap-3 sm:grid-cols-2">
          {items.map((item) => {
            const data = milestones[item.key];
            if (!data) return null;
            const ok = data.reached === true;
            return (
              <li key={item.key} className={`rounded-xl border p-3 ${ok ? 'border-[#cfe6dc] bg-[#e5f3ed]' : 'border-black/6 bg-white'}`}>
                <p className="text-sm font-semibold text-[#3a4a46]">{item.label}</p>
                {data.capitalNeeded === null ? (
                  <p className="mt-1 text-xs text-[#7b8784]">{t('fireMilestoneLeanUnset')}</p>
                ) : <>
                  <p className="mt-1 text-xs text-[#7b8784]">{formatEuro(data.capitalNeeded)}</p>
                  <p className={`mt-1 text-xs font-medium ${ok ? 'text-[#2d7b65]' : 'text-[#bd5e46]'}`}>
                    {item.key === 'lean' && leanNote ? leanNote : ok ? t('fireMilestoneReached')
                      : t('fireMilestoneMissing', { amount: formatEuro(Math.max(data.capitalNeeded - capital, 0)) })}
                  </p>
                </>}
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
