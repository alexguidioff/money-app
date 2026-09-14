import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { translations, type Lang } from '@/lib/translations';

const LINGUE: Lang[] = ['it', 'en', 'de', 'es', 'fr'];
const italiano = translations.it as Record<string, unknown>;

/** Le traduzioni sono testi, o record selezionati da un parametro ({ Expenses, Income, Savings }). */
function testi(valore: unknown): string[] {
  return typeof valore === 'string' ? [valore]
    : valore && typeof valore === 'object' ? Object.values(valore).filter((v): v is string => typeof v === 'string') : [];
}
const segnaposto = (testo: string) => [...testo.matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]).sort().join(',');

describe('traduzioni', () => {
  it('ogni testo ha gli stessi segnaposto in tutte le lingue', () => {
    // Un {{amount}} dimenticato in una lingua lascia la frase senza la cifra.
    const diversi = LINGUE.slice(1).flatMap((lang) => Object.keys(italiano).flatMap((chiave) => {
      const tabella = translations[lang] as Record<string, unknown>;
      const a = testi(italiano[chiave]).map(segnaposto).join('|');
      const b = testi(tabella[chiave]).map(segnaposto).join('|');
      return a === b ? [] : [`${lang}.${chiave}: ${a} ≠ ${b}`];
    }));
    expect(diversi).toEqual([]);
  });

  it('nessun segnaposto a graffa singola', () => {
    // L'interpolazione vuole {{x}}: con {x} la pagina stampa "{x}" letterale.
    const sbagliati = LINGUE.flatMap((lang) => Object.entries(translations[lang] as Record<string, unknown>)
      .flatMap(([chiave, valore]) => testi(valore).some((t) => /(^|[^{])\{\w+\}([^}]|$)/.test(t)) ? [`${lang}.${chiave}`] : []));
    expect(sbagliati).toEqual([]);
  });

  it('nessun testo vuoto e nessun record con voci diverse fra lingue', () => {
    const problemi = LINGUE.flatMap((lang) => Object.keys(italiano).flatMap((chiave) => {
      const valore = (translations[lang] as Record<string, unknown>)[chiave];
      if (testi(valore).some((t) => !t.trim()) || testi(valore).length === 0) return [`${lang}.${chiave}: vuoto`];
      if (typeof italiano[chiave] === 'object' && italiano[chiave] && valore && typeof valore === 'object'
          && Object.keys(italiano[chiave] as object).sort().join() !== Object.keys(valore).sort().join()) {
        return [`${lang}.${chiave}: voci diverse`];
      }
      return [];
    }));
    expect(problemi).toEqual([]);
  });

  it('in italiano gli accenti sono accenti, non apostrofi', () => {
    // "e'", "piu'", "gia\u2019": vanno bene nei commenti del codice, non a video.
    const sbagliati = Object.entries(italiano).flatMap(([chiave, valore]) => testi(valore)
      .flatMap((t) => [...t.matchAll(/\b(?!po['\u2019])[A-Za-z]*[aeiouAEIOU]['\u2019](?![A-Za-zÀ-ÿ])/g)].map((m) => `${chiave}: ${m[0]}`)));
    expect(sbagliati).toEqual([]);
  });

  it('in francese si da\' del tu, come nelle altre lingue', () => {
    const sbagliati = Object.entries(translations.fr as Record<string, unknown>)
      .flatMap(([chiave, valore]) => testi(valore).some((t) => /\b(vous|votre|vos)\b/i.test(t)) ? [chiave] : []);
    expect(sbagliati).toEqual([]);
  });

  it('nessun testo lungo rimasto in italiano nelle altre lingue', () => {
    // Una chiave aggiunta copiando l'italiano in tutte le lingue compila e passa
    // inosservata: e' cosi' che la nota fiscale italiana compariva in inglese.
    const copiati = LINGUE.slice(1).flatMap((lang) => Object.keys(italiano).flatMap((chiave) => {
      const a = testi(italiano[chiave]).join('|');
      const b = testi((translations[lang] as Record<string, unknown>)[chiave]).join('|');
      return a.length > 20 && a === b ? [`${lang}.${chiave}`] : [];
    }));
    expect(copiati).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Testi senza uso: il controllo fatto a mano che ne aveva trovati 106.

function sorgenti(cartella: string, estensioni: RegExp): string[] {
  return readdirSync(cartella).flatMap((nome) => {
    const percorso = join(cartella, nome);
    if (statSync(percorso).isDirectory()) return sorgenti(percorso, estensioni);
    return estensioni.test(nome) ? [percorso] : [];
  });
}

describe('testi senza uso', () => {
  it('ogni chiave e\' usata dal codice, composta da un prefisso dichiarato o e\' un codice del server', () => {
    const frontend = ['components', 'lib', 'app']
      .flatMap((cartella) => sorgenti(cartella, /\.(ts|tsx)$/))
      .filter((file) => !file.endsWith('translations.ts'))
      .map((file) => readFileSync(file, 'utf-8')).join('\n');
    const backend = sorgenti('backend/app', /\.py$/).map((file) => readFileSync(file, 'utf-8')).join('\n');
    // Codici che il server manda e che `responseError` o l'anteprima dell'import
    // traducono cercandoli fra le chiavi: non compaiono come stringa nel frontend.
    const codiciServer = new Set([...backend.matchAll(/(?:detail=|"code":\s*|ValueError\()"(\w+)"/g)].map((m) => m[1]));
    // Chiavi composte a runtime: `t(\`debtIssue_${motivo}\`)`.
    const prefissiDinamici = ['debtIssue_'];
    const orfane = Object.keys(italiano).filter((chiave) =>
      !new RegExp(`['"\`]${chiave}['"\`]`).test(frontend)
      && !prefissiDinamici.some((prefisso) => chiave.startsWith(prefisso))
      && !codiciServer.has(chiave));
    expect(orfane).toEqual([]);
  });
});
