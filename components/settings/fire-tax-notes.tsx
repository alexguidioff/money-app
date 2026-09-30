'use client';

import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useI18n } from '@/lib/i18n-context';
import { COUNTRIES, countryLabel, taxNoteFor } from '@/lib/data/countries';

// Niente calcolo fiscale: l'aliquota sui prelievi la dichiara l'utente nel
// profilo. Qui si mostrano solo gli avvisi di testo che aiutano a ricordare
// le trappole note di ogni paese (plusvalenze, patrimoniali, box 3 NL...).
export function FireTaxNotes({ profileCountry }: { profileCountry: string }) {
  const { t, lang } = useI18n();
  const [country, setCountry] = useState(profileCountry || 'IT');
  const note = taxNoteFor(country, lang);
  const countryName = countryLabel(country, lang);

  return (
    <Card className="border-[var(--money-velo)]/6 bg-[var(--money-superficie)] shadow-sm shadow-black/[0.025]">
      <CardHeader>
        <CardTitle className="text-[17px]">{t('fireTaxNotesTitle')}</CardTitle>
        <p className="mt-1 text-xs text-[var(--money-testo-tenue)]">{t('fireTaxNotesSubtitle')}</p>
      </CardHeader>
      <CardContent className="space-y-3">
        <select value={country} onChange={(e) => setCountry(e.target.value)}
          className="h-10 w-full rounded-lg border border-input bg-[var(--money-superficie)] px-2.5 text-sm outline-none focus:border-ring">
          {COUNTRIES.map((c) => (
            <option key={c.code} value={c.code}>{countryLabel(c.code, lang)}</option>
          ))}
        </select>
        {note ? (
          <div className="rounded-xl border border-[var(--money-allarme-bordo)] bg-[var(--money-allarme-tenue)] p-4 text-sm text-[var(--money-testo)]">
            <p className="mb-1 text-xs font-semibold text-[var(--money-allarme)]">{countryName}</p>
            <p className="leading-relaxed">{note.body}</p>
          </div>
        ) : (
          <p className="rounded-xl border border-dashed border-[var(--money-velo)]/10 bg-[var(--money-superficie-tenue)] px-4 py-6 text-center text-sm text-[var(--money-testo-tenue)]">{t('fireTaxNotesEmpty')}</p>
        )}
      </CardContent>
    </Card>
  );
}
