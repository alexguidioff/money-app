import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Il contrasto del testo, misurato sul sorgente invece che a occhio.
 *
 * La soglia e' 4,5:1, quella che WCAG chiede al testo normale: sotto, il testo
 * c'e' ma non si legge, e nessun test funzionale se ne accorge. I colori qui
 * sotto li legge da `app/globals.css`: la tavolozza di giorno sta in `:root`,
 * quella notturna in `html.dark`, e leggerle invece di ricopiarle vuol dire
 * che il giorno in cui qualcuno abbassa un fondo e scurisce troppo un testo,
 * e' questo file a dirlo - non una segnalazione di Mary.
 *
 * I colori dei sei temi sono l'eccezione: vivono in JS (THEMES,
 * money-dashboard.tsx) e sono copiati qui. Se cambiano li', vanno cambiati
 * anche qui, ed e' voluto.
 */
const CSS = readFileSync('app/globals.css', 'utf-8');

/** Le variabili `--money-...` dichiarate con un colore pieno (non `var(...)`). */
function tavolozza(testo: string): Record<string, string> {
  const lette: Record<string, string> = {};
  for (const [, nome, colore] of testo.matchAll(/(--money-[a-z-]+):\s*(#[0-9a-fA-F]{3,6});/g)) {
    lette[nome] = colore.toLowerCase();
  }
  return lette;
}

const TAGLIO = CSS.indexOf('html.dark {');
const GIORNO = tavolozza(CSS.slice(0, TAGLIO));
const NOTTE = tavolozza(CSS.slice(TAGLIO));
// Le sei pagine tinteggiate di notte: una regola per tema.
const PAGINE_NOTTE = [...CSS.slice(TAGLIO).matchAll(/html\.dark\[data-tema='[^']+'\]\s*\{\s*--money-page:\s*(#[0-9a-fA-F]{6})/g)]
  .map(([, colore]) => colore.toLowerCase());

/** I fondi su cui puo' capitare del testo: la card, i riquadri dentro, le
 *  tinte delle famiglie. Fuori restano i veli (traslucidi) e le linee di un
 *  pixel, che non sono fondo di niente. */
const FONDI = ['--money-superficie', '--money-superficie-tenue', '--money-superficie-hover',
  '--money-ok-tenue', '--money-allarme-tenue', '--money-allarme-velo',
  '--money-attenzione-tenue', '--money-risparmio-tenue', '--money-investimento-tenue'];

/** I colori che l'app usa per scrivere. Fuori restano quelli che non sono
 *  testo: il velo, la linea, l'anello dei campi, il riempimento di una barra
 *  e il dato che manca in un grafico. */
const INCHIOSTRI = ['--money-testo', '--money-testo-tenue', '--money-testo-muto', '--money-marca',
  '--money-ok', '--money-allarme', '--money-allarme-hover', '--money-attenzione',
  '--money-risparmio', '--money-investimento'];

// Le pagine dei sei temi, chiare e scure: un testo puo' capitare anche li'.
const TEMI_CHIARI = ['#f9f8f3', '#f9f5f3', '#f3f9f6', '#f3f8f9', '#f3f5f9', '#f6f3f9'];
const TEMI_SCURI = ['#5c501f', '#5c311f', '#1f5c38', '#1f545c', '#1f355c', '#3f1f5c', // --money-deep
  '#332c0f', '#331a0f', '#0f331e', '#0f2f33', '#0f1c33', '#220f33'];                   // --money-sidebar

const SFONDI_CHIARI = ['#ffffff', '#f4f5f1', '#f9f8f3', '#f6f8f6', '#fafaf8', '#edf0ed',
  '#f4f9f7', '#f9fbf9', '#f6f3f9', '#f0f2ee', '#fce9e3', '#fff6f3', '#fdf6ec', '#eaf5ef',
  '#e5f3ed', '#fff9f6', '#f2efdb', '#f6f9f7', '#f0f8f4', '#f3faf7', '#f2f7f4', '#f7f8f5'];
// Fuori resta `#dfe4e1`: e' la linea di un pixel che stacca un ramo dall'altro, non
// un fondo su cui sta del testo, e includerlo accuserebbe un colore che nessuno legge.

/** I colori che possono stare sotto la soglia perche' non sono testo da leggere:
 *  stati spenti (una voce disattivata, un gettone inattivo, che WCAG esenta) e
 *  icone decorative accanto a un testo che dice gia' la stessa cosa. */
const SPENTI = new Set(['#a3adaa', '#9aa5a2']);
const ICONE = new Set(['#8c9a95', '#b3bcb8', '#c3ccc8', '#b6bdba']);
/** Il testo chiaro scritto per i riquadri scuri: si misura sui temi scuri. */
const SU_FONDO_SCURO = new Set(['#f6c6b0']);

const SOGLIA = 4.5;

function lineare(valore: number): number {
  const c = valore / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function luminanza(colore: string): number {
  const h = colore.replace('#', '');
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  return 0.2126 * lineare(r) + 0.7152 * lineare(g) + 0.0722 * lineare(b);
}

function contrasto(primo: string, secondo: string): number {
  const a = luminanza(primo);
  const b = luminanza(secondo);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

/** Il bianco al 70% sopra un fondo scuro: quello che l'occhio vede e' la media. */
function sovrapposto(alfa: number, fondo: string): string {
  const f = fondo.replace('#', '');
  const canali = [0, 2, 4].map((i) => Math.round(255 * alfa + parseInt(f.slice(i, i + 2), 16) * (1 - alfa)));
  return `#${canali.map((c) => c.toString(16).padStart(2, '0')).join('')}`;
}

function sorgenti(cartella: string): string[] {
  return readdirSync(cartella).flatMap((nome) => {
    const percorso = join(cartella, nome);
    if (statSync(percorso).isDirectory()) return sorgenti(percorso);
    return /\.tsx$/.test(nome) ? [percorso] : [];
  });
}

const testo = ['components', 'app'].flatMap(sorgenti).map((f) => ({ file: f, codice: readFileSync(f, 'utf-8') }));

/** Il peggior contrasto di ogni inchiostro sui fondi di una modalita'. */
function peggiori(tavolozza: Record<string, string>, fondi: string[]): string[] {
  const esiti: string[] = [];
  for (const inchiostro of INCHIOSTRI) {
    const colore = tavolozza[inchiostro];
    if (!colore) { esiti.push(`${inchiostro}: non dichiarato`); continue; }
    const minimo = Math.min(...fondi.map((fondo) => contrasto(colore, fondo)));
    if (minimo < SOGLIA) esiti.push(`${inchiostro} ${colore} = ${minimo.toFixed(2)}:1`);
  }
  return esiti;
}

describe('contrasto del testo', () => {
  it('di giorno ogni inchiostro arriva a 4,5:1 sui fondi chiari', () => {
    expect(peggiori(GIORNO, [...SFONDI_CHIARI, ...TEMI_CHIARI])).toEqual([]);
  });

  it('di notte ogni inchiostro arriva a 4,5:1 sui fondi scuri', () => {
    // Fuori i temi scuri: le card profonde sono scure in tutte e due le
    // modalita' e ci sta sopra solo del testo bianco, mai l'inchiostro
    // dell'app. Metterli qui vorrebbe dire accusare un incontro che non
    // succede.
    const fondi = [...FONDI.map((nome) => NOTTE[nome]), ...PAGINE_NOTTE];
    expect(PAGINE_NOTTE).toHaveLength(6);
    expect(fondi.every((fondo) => fondo && fondo.startsWith('#'))).toBe(true);
    expect(peggiori(NOTTE, fondi)).toEqual([]);
  });

  it('la notte ha davvero abbassato i fondi, non solo rinominato i colori', () => {
    // Senza questo, una tavolozza notturna copiata da quella di giorno
    // passerebbe il test di sopra senza essere una notte.
    for (const fondo of FONDI) {
      expect(luminanza(NOTTE[fondo])).toBeLessThan(luminanza(GIORNO[fondo]));
    }
    expect(luminanza(NOTTE['--money-page'])).toBeLessThan(0.05);
  });

  it('ogni colore scritto a mano in una classe text-[...] arriva a 4,5:1 sui fondi chiari', () => {
    const peggiori: string[] = [];
    for (const { file, codice } of testo) {
      for (const [, colore] of codice.matchAll(/text-\[(#[0-9a-fA-F]{6})\]/g)) {
        const c = colore.toLowerCase();
        if (SPENTI.has(c) || ICONE.has(c)) continue;
        const sfondi = SU_FONDO_SCURO.has(c) ? TEMI_SCURI : SFONDI_CHIARI;
        const minimo = Math.min(...sfondi.map((fondo) => contrasto(c, fondo)));
        if (minimo < SOGLIA) peggiori.push(`${file}: ${c} = ${minimo.toFixed(2)}:1`);
      }
    }
    expect([...new Set(peggiori)]).toEqual([]);
  });

  it('il testo bianco sui fondi scuri sta almeno al 70%, su tutti i temi', () => {
    const deboli: string[] = [];
    for (const { file, codice } of testo) {
      for (const [, alfa] of codice.matchAll(/text-white\/(\d+)/g)) {
        const a = Number(alfa) / 100;
        if (a >= 0.7) continue;
        const minimo = Math.min(...TEMI_SCURI.map((fondo) => contrasto(sovrapposto(a, fondo), fondo)));
        deboli.push(`${file}: text-white/${alfa} = ${minimo.toFixed(2)}:1 sul tema peggiore`);
      }
    }
    expect([...new Set(deboli)]).toEqual([]);
  });

  it('la soglia morde: il grigio di prima e il bianco al 35% non passerebbero', () => {
    // Senza questo, i due test sopra passerebbero anche con la formula sbagliata.
    expect(Math.min(...SFONDI_CHIARI.map((f) => contrasto('#87918e', f)))).toBeLessThan(SOGLIA);
    const al35 = sovrapposto(0.35, '#5c501f');
    expect(contrasto(al35, '#5c501f')).toBeLessThan(SOGLIA);
  });
});
