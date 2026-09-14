'use client';

import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useI18n } from '@/lib/i18n-context';
import { COUNTRIES, TAX_NOTES, countryByCode } from '@/lib/data/countries';

// Niente calcolo fiscale: l'aliquota sui prelievi la dichiara l'utente nel
// profilo. Qui si mostrano solo gli avvisi di testo che aiutano a ricordare
// le trappole note di ogni paese (plusvalenze, patrimoniali, box 3 NL...).
export function FireTaxNotes({ profileCountry }: { profileCountry: string }) {
  const { t, lang } = useI18n();
  const [country, setCountry] = useState(profileCountry || 'IT');
  const note = TAX_NOTES.find((n) => n.country === country && n.language === lang)
    ?? TAX_NOTES.find((n) => n.country === country && n.language === 'it');
  const countryName = countryByCode(country)?.[`name${lang.charAt(0).toUpperCase() + lang.slice(1)}` as keyof ReturnType<typeof countryByCode>] ?? country;

  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardHeader>
        <CardTitle className="text-[17px]">{t('fireTaxNotesTitle')}</CardTitle>
        <p className="mt-1 text-xs text-[#7b8784]">{t('fireTaxNotesSubtitle')}</p>
      </CardHeader>
      <CardContent className="space-y-3">
        <select value={country} onChange={(e) => setCountry(e.target.value)}
          className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm outline-none focus:border-ring">
          {COUNTRIES.map((c) => (
            <option key={c.code} value={c.code}>{c[`name${lang.charAt(0).toUpperCase() + lang.slice(1)}` as keyof typeof c]}</option>
          ))}
        </select>
        {note ? (
          <div className="rounded-xl border border-[#f4d8ce] bg-[#fce9e3] p-4 text-sm text-[#3a4a46]">
            <p className="mb-1 text-xs font-semibold text-[#bd5e46]">{countryName}</p>
            <p className="leading-relaxed">{note.body}</p>
          </div>
        ) : (
          <p className="rounded-xl border border-dashed border-black/10 bg-[#fafaf8] px-4 py-6 text-center text-sm text-[#7b8784]">{t('fireTaxNotesEmpty')}</p>
        )}
      </CardContent>
    </Card>
  );
}
