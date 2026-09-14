'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { LOCALE_MAP, translations, type Lang, type TranslationKey } from './translations';
import { etichettaPeriodo } from '@/lib/period-label';

const STORAGE_KEY = 'money-app-lang';
const SUPPORTED_LANGS: Lang[] = ['it', 'en', 'de', 'es', 'fr'];

function detectInitialLang(): Lang {
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
  formatNumber: (value: number, options?: Intl.NumberFormatOptions) => string;
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

  const formatNumber = useCallback(
    (value: number, options?: Intl.NumberFormatOptions) => new Intl.NumberFormat(locale, options).format(value),
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
    () => ({ lang, setLang, t, locale, formatEuro, formatCompactEuro, formatNumber, formatDate, monthNames, monthNamesShort, formatPeriodLabel }),
    [lang, setLang, t, locale, formatEuro, formatCompactEuro, formatNumber, formatDate, monthNames, monthNamesShort, formatPeriodLabel],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error('useI18n must be used within I18nProvider');
  return ctx;
}
