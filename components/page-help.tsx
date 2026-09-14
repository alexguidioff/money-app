'use client';

import { useState } from 'react';
import { HelpCircle, X } from 'lucide-react';
import type { TranslationKey } from '@/lib/translations';
import { useI18n } from '@/lib/i18n-context';

/**
 * La spiegazione della pagina, dietro un punto interrogativo.
 *
 * Dice a cosa serve la pagina e da cosa dipende per funzionare: quasi tutte
 * mostrano numeri che nascono altrove, e quando restano vuote la causa e' che
 * manca quell'altra cosa. Scriverlo qui evita di doverlo indovinare.
 */
export function PageHelp({ titolo, testo, dipendenza }: {
  titolo: TranslationKey;
  testo: TranslationKey;
  dipendenza?: TranslationKey;
}) {
  const { t } = useI18n();
  const [aperto, setAperto] = useState(false);

  return (
    <span className="relative inline-flex">
      <button
        type="button"
        onClick={() => setAperto((valore) => !valore)}
        aria-expanded={aperto}
        aria-label={t('helpAria')}
        title={t('helpAria')}
        className="ml-2 inline-flex size-5 items-center justify-center rounded-full text-[#a3adaa] transition hover:bg-black/5 hover:text-[#3d4a47]"
      >
        <HelpCircle className="size-[18px]" />
      </button>

      {aperto && (
        <>
          {/* Un velo trasparente: cliccando fuori si chiude, senza dover
              centrare di nuovo il punto interrogativo. */}
          <span className="fixed inset-0 z-40" onClick={() => setAperto(false)} />
          {/* Le spiegazioni sono elenchi: gli a capo si rispettano, e un testo
              lungo scorre dentro il riquadro invece di uscire dallo schermo,
              anche su un telefono. */}
          <span className="absolute left-0 top-8 z-50 block max-h-[70vh] w-[24rem] max-w-[calc(100vw-2.5rem)] overflow-y-auto rounded-xl border border-black/7 bg-white p-4 text-left shadow-xl">
            <span className="flex items-start justify-between gap-3">
              <span className="text-sm font-semibold text-[#173b33]">{t(titolo)}</span>
              <button type="button" onClick={() => setAperto(false)} aria-label={t('close')}
                      className="text-[#a3adaa] transition hover:text-[#3d4a47]">
                <X className="size-3.5" />
              </button>
            </span>
            <span className="mt-2 block whitespace-pre-line text-xs leading-5 text-[#52615d]">{t(testo)}</span>
            {dipendenza && (
              <span className="mt-2.5 block whitespace-pre-line rounded-lg bg-[#f4f5f1] px-3 py-2 text-xs leading-5 text-[#71807c]">
                {t(dipendenza)}
              </span>
            )}
          </span>
        </>
      )}
    </span>
  );
}
