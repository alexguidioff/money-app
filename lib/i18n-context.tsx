'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { LOCALE_MAP, translations, type Lang, type TranslationKey } from './translations';
import { etichettaPeriodo } from '@/lib/period-label';

const STORAGE_KEY = 'money-app-lang';
const SUPPORTED_LANGS: Lang[] = ['it', 'en', 'de', 'es', 'fr'];

/** La lingua scelta, o quella del browser se non ne hai ancora scelta una.
 *  Esportata perche' serve anche fuori dal provider: il confine d'errore
 *  (`components/error-fallback.tsx`) sta dove il provider non c'e' piu'. */
export function detectInitialLang(): Lang {
  if (typeof window === 'undefined') return 'it';
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored && SUPPORTED_LANGS.includes(stored as Lang)) return stored as Lang;
  } catch {
    // localStorage non disponibile (privacy mode, ecc.): ignora e passa al fallback browser.
  }
  const browserLang = (navigator.language || 'it').slice(0, 2).toLowerCase();
  if (SUPPORTED_LANGS.includes(browserLang as Lang)) return browserLang as Lang;
  return 'it';
}

type TranslateParams = Record<string, string | number>;

type I18nContextValue = {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: TranslationKey, params?: TranslateParams) => string;
  locale: string;
  formatEuro: (value: number) => string;
  formatCompactEuro: (value: number) => string;
  /**
   * Una cifra nella valuta che le appartiene.
   *
   * Il saldo di un conto in franchi e' in franchi: mostrarlo come euro sarebbe
   * un numero sbagliato con la forma giusta. I totali invece si mostrano sempre
   * in euro, e per quelli restano `formatEuro` e `formatCompactEuro`.
   */
  formatMoney: (value: number, currency: string) => string;
  /** Come `formatMoney`, per gli assi e le etichette dei grafici: senza
   *  centesimi, che su un asse non si leggono. */
  formatCompactMoney: (value: number, currency: string) => string;
  formatNumber: (value: number, options?: Intl.NumberFormatOptions) => string;
  /**
   * Le percentuali si scrivono in un modo solo: un decimale al massimo, col
   * separatore decimale della lingua scelta.
   *
   * Due nomi e non uno perche' il codice ha in mano due unita' diverse - 0,425
   * e 42,5 - e chiamarle tutte e due "percentuale" e' esattamente come sono
   * nate tre precisioni diverse nella stessa schermata e un "12.5%" col punto
   * in italiano. Chi scrive la chiamata vede l'unita' nel nome.
   *
   * Dove il `%` lo mette gia' il testo della traduzione (`{{percent}}%`) si usa
   * `formatPercentNumber`: la formattazione e' la stessa, il segno lo aggiunge
   * la frase.
   */
  formatPercentRatio: (value: number, options?: Intl.NumberFormatOptions) => string;
  formatPercentPoints: (value: number, options?: Intl.NumberFormatOptions) => string;
  /** Il numero della percentuale, senza il segno: il `%` lo mette la frase. */
  formatPercentNumber: (value: number) => string;
  formatDate: (value: Date | string, options?: Intl.DateTimeFormatOptions) => string;
  monthNames: string[];
  monthNamesShort: string[];
  /** Le etichette di periodo del server ("Gen 25", "T1 25") nella lingua scelta. */
  formatPeriodLabel: (label: unknown) => string;
};

const I18nContext = createContext<I18nContextValue | null>(null);

type Resolved = string | Record<string, string>;

function resolveTemplate(template: Resolved, params?: TranslateParams): string {
  // Alcune traduzioni sono record (es. { Expenses, Income, Savings }): il primo
  // parametro numerico o stringa seleziona la chiave e poi si interpola.
  if (template && typeof template === 'object') {
    if (!params) return '';
    const selector = params['0'];
    if (typeof selector === 'string' || typeof selector === 'number') {
      const found = template[String(selector)];
      if (typeof found === 'string') return interpolate(found, params);
    }
    return '';
  }
  return interpolate(template, params);
}

function interpolate(template: string, params?: TranslateParams): string {
  if (!params) return template;
  return template.replace(/\{\{(\w+)\}\}/g, (match, key: string) => {
    const value = params[key];
    return value === undefined ? match : String(value);
  });
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>('it');

  useEffect(() => {
    setLangState(detectInitialLang());
  }, []);

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Persistenza best-effort: se localStorage non è disponibile la scelta vale solo per la sessione corrente.
    }
  }, []);

  // La lingua della pagina, non solo quella dei suoi testi. `app/layout.tsx`
  // scrive `lang="it"` una volta sola, e da li' non si muove: scegliendo il
  // tedesco, un lettore di schermo continuava a leggere il tedesco con la
  // pronuncia italiana e il correttore del browser correggeva parole giuste.
  // Il server non puo' saperla - la scelta sta in localStorage, che il server
  // non legge - quindi la allinea il browser appena sa quale hai scelto. Il
  // default scritto nel layout resta "it" ed e' quello che vede chi arriva
  // senza JavaScript.
  useEffect(() => {
    document.documentElement.lang = LOCALE_MAP[lang];
  }, [lang]);

  const t = useCallback((key: TranslationKey, params?: TranslateParams) => {
    const table = translations[lang] ?? translations.it;
    const template = (table[key] ?? translations.it[key] ?? key) as Resolved;
    return resolveTemplate(template, params);
  }, [lang]);

  const locale = LOCALE_MAP[lang];

  const formatEuro = useCallback(
    (value: number) => new Intl.NumberFormat(locale, { style: 'currency', currency: 'EUR', minimumFractionDigits: 2 }).format(value),
    [locale],
  );

  const formatCompactEuro = useCallback(
    (value: number) => new Intl.NumberFormat(locale, { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 }).format(value),
    [locale],
  );

  const formatMoney = useCallback((value: number, currency: string) => {
    const codice = (currency || 'EUR').trim().toUpperCase();
    try {
      return new Intl.NumberFormat(locale, { style: 'currency', currency: codice, minimumFractionDigits: 2 }).format(value);
    } catch {
      // Intl conosce solo le valute con un codice ISO: una sigla come BTC lo fa
      // esplodere, e una pagina che si rompe per un'etichetta e' peggio di una
      // cifra con la sigla accanto.
      return `${new Intl.NumberFormat(locale, { minimumFractionDigits: 2 }).format(value)} ${codice}`;
    }
  }, [locale]);

  const formatCompactMoney = useCallback((value: number, currency: string) => {
    const codice = (currency || 'EUR').trim().toUpperCase();
    try {
      return new Intl.NumberFormat(locale, { style: 'currency', currency: codice, maximumFractionDigits: 0 }).format(value);
    } catch {
      // Stesso motivo di `formatMoney`: una sigla che Intl non conosce non deve
      // portarsi via l'asse di un grafico.
      return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(value)} ${codice}`;
    }
  }, [locale]);

  const formatNumber = useCallback(
    (value: number, options?: Intl.NumberFormatOptions) => new Intl.NumberFormat(locale, options).format(value),
    [locale],
  );

  const formatPercentRatio = useCallback(
    (value: number, options?: Intl.NumberFormatOptions) =>
      new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 1, ...options }).format(value),
    [locale],
  );

  const formatPercentPoints = useCallback(
    (value: number, options?: Intl.NumberFormatOptions) => formatPercentRatio(value / 100, options),
    [formatPercentRatio],
  );

  // Stessa precisione delle altre due, scritta per forza due volte: questa non
  // usa lo stile `percent` (niente segno), quindi non puo' passare da quelle.
  const formatPercentNumber = useCallback(
    (value: number) => new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value),
    [locale],
  );

  const formatDate = useCallback(
    (value: Date | string, options?: Intl.DateTimeFormatOptions) => {
      const date = typeof value === 'string' ? new Date(value) : value;
      return date.toLocaleDateString(locale, options);
    },
    [locale],
  );

  const monthNames = useMemo(() => {
    const formatter = new Intl.DateTimeFormat(locale, { month: 'long' });
    return Array.from({ length: 12 }, (_, index) => {
      const name = formatter.format(new Date(2024, index, 1));
      return name.charAt(0).toUpperCase() + name.slice(1);
    });
  }, [locale]);

  const monthNamesShort = useMemo(() => {
    const formatter = new Intl.DateTimeFormat(locale, { month: 'short' });
    return Array.from({ length: 12 }, (_, index) => {
      const name = formatter.format(new Date(2024, index, 1)).replace(/\.$/, '');
      return name.charAt(0).toUpperCase() + name.slice(1);
    });
  }, [locale]);

  const formatPeriodLabel = useCallback((label: unknown) => etichettaPeriodo(label, monthNamesShort, lang),
    [monthNamesShort, lang]);

  const value = useMemo(
    () => ({ lang, setLang, t, locale, formatEuro, formatCompactEuro, formatMoney, formatCompactMoney, formatNumber, formatPercentRatio, formatPercentPoints, formatPercentNumber, formatDate, monthNames, monthNamesShort, formatPeriodLabel }),
    [lang, setLang, t, locale, formatEuro, formatCompactEuro, formatMoney, formatCompactMoney, formatNumber, formatPercentRatio, formatPercentPoints, formatPercentNumber, formatDate, monthNames, monthNamesShort, formatPeriodLabel],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error('useI18n must be used within I18nProvider');
  return ctx;
}
