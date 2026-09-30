'use client';

import { useEffect } from 'react';
import { TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { detectInitialLang } from '@/lib/i18n-context';
import { LOCALE_MAP, translations, type Lang, type TranslationKey } from '@/lib/translations';

/**
 * Quello che si vede quando una schermata non riesce a disegnarsi.
 *
 * Senza, Next mostra la sua pagina: in inglese, con le sue parole e senza i
 * colori dell'app - e per chi la legge e' un guasto dell'app, non un guasto
 * raccontato dall'app. Questo componente lo monta Next **al posto** della
 * pagina, quindi `useI18n` qui non c'e': le parole si prendono dalla tabella
 * della lingua scelta, che sta in localStorage, l'unica cosa che sopravvive a
 * un albero che non c'e' piu'.
 *
 * `app/global-error.tsx` lo usa identico, dentro `<html>` e `<body>` che
 * scrive lui: quando a rompersi e' il layout, il layout non c'e' piu'.
 */
export function ErrorFallback({ onRetry }: { onRetry: () => void }) {
  const lang = detectInitialLang();

  // La lingua della pagina, come la mette il provider quando l'albero c'e':
  // qui il provider e' caduto con il resto, quindi la allinea questo.
  useEffect(() => {
    document.documentElement.lang = LOCALE_MAP[lang];
  }, [lang]);

  return (
    <div className="grid min-h-[60vh] place-items-center px-6 py-10">
      <div className="w-full max-w-md rounded-2xl border border-[var(--money-velo)]/6 bg-[var(--money-superficie)] p-6 text-center shadow-sm">
        <span className="mx-auto grid size-11 place-items-center rounded-xl bg-[var(--money-allarme-tenue)] text-[var(--money-allarme)]">
          <TriangleAlert className="size-5" />
        </span>
        <h1 className="mt-4 text-[17px] font-semibold text-[var(--money-marca)]">{testo('errorTitle', lang)}</h1>
        <p className="mt-2 text-sm leading-6 text-[var(--money-testo-tenue)]">{testo('errorBody', lang)}</p>
        <div className="mt-5 flex flex-wrap justify-center gap-2">
          <Button type="button" onClick={onRetry} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">
            {testo('errorRetry', lang)}
          </Button>
          <Button type="button" variant="outline" onClick={() => window.location.reload()}>
            {testo('errorReload', lang)}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** La stessa risoluzione del provider, ma senza provider: le chiavi qui non
 *  hanno segnaposto, e sotto c'e' sempre l'italiano. */
function testo(chiave: TranslationKey, lang: Lang): string {
  const tabella = translations[lang] ?? translations.it;
  const valore = tabella[chiave] ?? translations.it[chiave];
  return typeof valore === 'string' ? valore : chiave;
}
