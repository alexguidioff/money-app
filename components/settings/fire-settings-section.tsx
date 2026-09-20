'use client';

import { useEffect, useState } from 'react';
import { RetirementProfileForm, type RetirementProfile } from './retirement-profile-form';
import { IncomeStreamsForm } from './income-streams-form';
import { FireTaxNotes } from './fire-tax-notes';
import { ExpensesByCategory } from '@/components/fire/expenses-by-category';
import { useI18n } from '@/lib/i18n-context';

// Seconda scheda della pagina Pensionamento e FIRE: profilo, spese in pensione
// per categoria, flussi attesi e note fiscali, ognuno con la sua card. Le note
// fiscali mostrano il paese del profilo come default; senza profilo, IT.

export function FireSettingsSection({ apiUrl }: { apiUrl: string }) {
  const { t } = useI18n();
  const [profile, setProfile] = useState<RetirementProfile | null>(null);
  // Cambia a ogni salvataggio del profilo: la base di spesa decide quali anni
  // e quali importi mostra la card delle categorie, che va ricaricata.
  const [versione, setVersione] = useState(0);

  // Recupera il profilo solo per sapere il paese (le note fiscali lo usano
  // come default). Se fallisce o non c'e' profilo, si ripiega su IT.
  useEffect(() => {
    const controller = new AbortController();
    fetch(`${apiUrl}/api/fire/profile`, { signal: controller.signal })
      .then((r) => r.ok ? r.json() as Promise<RetirementProfile> : null)
      .then((data) => setProfile(data))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        // Nessun profilo o errore di rete: le note fiscali useranno IT.
      });
    return () => controller.abort();
  }, [apiUrl]);

  return (
    <div className="space-y-5">
      {/* Il titolo e' il nome della scheda, non quello della pagina: chi sta
          dentro "Profilo e flussi" e legge "Pensionamento e FIRE" crede di
          essere tornato all'inizio della pagina. */}
      <div>
        <h2 className="flex items-center text-lg font-semibold tracking-[-0.02em]">{t('fireTabSettings')}</h2>
        <p className="mt-1 text-xs text-[#5e6c68]">{t('fireSectionDesc')}</p>
      </div>
      <div className="grid items-start gap-5 xl:grid-cols-2">
        <div className="space-y-5">
          <RetirementProfileForm apiUrl={apiUrl} onSaved={(salvato) => { setProfile(salvato); setVersione((v) => v + 1); }} />
          <ExpensesByCategory key={versione} apiUrl={apiUrl} />
        </div>
        <div className="space-y-5">
          <IncomeStreamsForm apiUrl={apiUrl} />
          {/* La chiave rimonta le note quando il profilo arriva o cambia paese:
              il paese iniziale e' solo il valore di partenza del selettore. */}
          <FireTaxNotes key={profile?.country ?? 'IT'} profileCountry={profile?.country ?? 'IT'} />
        </div>
      </div>
    </div>
  );
}
