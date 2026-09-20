import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Il contrasto del testo, misurato sul sorgente invece che a occhio.
 *
 * La soglia e' 4,5:1, quella che WCAG chiede al testo normale: sotto, il testo
 * c'e' ma non si legge, e nessun test funzionale se ne accorge. I colori qui
 * sotto sono quelli che l'app usa davvero come fondo, e le sei terne dei temi
 * sono copiate da `THEMES` (money-dashboard.tsx): se cambiano li', vanno
 * cambiate anche qui, ed e' voluto - e' il punto in cui ci si accorge di aver
 * scurito un tema sotto la soglia.
 */
const SFONDI_CHIARI = ['#ffffff', '#f4f5f1', '#f9f8f3', '#f6f8f6', '#fafaf8', '#edf0ed',
  '#f4f9f7', '#f9fbf9', '#f6f3f9', '#f0f2ee', '#fce9e3', '#fff6f3', '#fdf6ec', '#eaf5ef',
  '#e5f3ed', '#fff9f6', '#f2efdb', '#f6f9f7', '#f0f8f4', '#f3faf7', '#f2f7f4', '#f7f8f5'];
// Fuori resta `#dfe4e1`: e' la linea di un pixel che stacca un ramo dall'altro, non
// un fondo su cui sta del testo, e includerlo accuserebbe un colore che nessuno legge.
const TEMI_SCURI = ['#5c501f', '#5c311f', '#1f5c38', '#1f545c', '#1f355c', '#3f1f5c', // --money-deep
  '#332c0f', '#331a0f', '#0f331e', '#0f2f33', '#0f1c33', '#220f33'];                   // --money-sidebar

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

describe('contrasto del testo', () => {
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
