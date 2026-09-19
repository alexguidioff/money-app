import { expect, test, type Page } from '@playwright/test';

/*
 * A larghezza da telefono nessuna pagina deve scorrere in orizzontale: e' il
 * primo segno di un layout pensato solo per lo schermo del computer. Tabelle e
 * grafici possono scorrere dentro il loro contenitore; la pagina no.
 */
test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

const PAGINE = ['Panoramica', 'Budget', 'Movimenti', 'Obiettivi', 'Patrimonio', 'Debiti', 'Investimenti',
  'Insieme', 'Appunti', 'Pensionamento e FIRE', 'Report', 'Impostazioni'];

async function apri(page: Page, voce: string) {
  const menu = page.getByRole('button', { name: /menu/i }).first();
  const voceMenu = page.locator('aside').getByRole('button', { name: voce, exact: true });
  if (!(await voceMenu.isVisible())) await menu.click();
  await voceMenu.click();
}

/** Gli elementi che sporgono oltre il bordo destro senza un contenitore che scorre. */
async function sporgenze(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const larghezza = document.documentElement.clientWidth;
    // Accettabile solo un contenitore che scorre (tabelle, grafici larghi). Uno
    // che nasconde cio' che sporge taglia un pulsante senza farlo vedere.
    const dentroScorrevole = (el: Element): boolean => {
      for (let n = el.parentElement; n; n = n.parentElement) {
        if (/(auto|scroll)/.test(getComputedStyle(n).overflowX) && n.scrollWidth > n.clientWidth + 1) return true;
      }
      return false;
    };
    const bordoVisibile = (el: Element): number => {
      let destra = larghezza;
      for (let n = el.parentElement; n; n = n.parentElement) {
        if (/(hidden|clip)/.test(getComputedStyle(n).overflowX)) destra = Math.min(destra, n.getBoundingClientRect().right);
      }
      return destra;
    };
    const fuori = [...document.querySelectorAll('body *')].filter((el) => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0 || getComputedStyle(el).position === 'fixed' || dentroScorrevole(el)) return false;
      if (getComputedStyle(el).visibility === 'hidden' || el.closest('[aria-hidden="true"], .sr-only, svg')) return false;
      return r.right > bordoVisibile(el) + 1;
    });
    // Solo i piu' esterni: un contenitore largo trascina fuori tutti i figli.
    const esterni = fuori.filter((el) => !fuori.some((altro) => altro !== el && altro.contains(el)));
    return esterni.slice(0, 8).map((el) => {
      const r = el.getBoundingClientRect();
      const testo = (el.textContent ?? '').trim().replace(/\s+/g, ' ').slice(0, 50);
      return `${el.tagName.toLowerCase()}.${String(el.className).split(' ').slice(0, 3).join('.')} right=${Math.round(r.right)} «${testo}»`;
    });
  });
}

/*
 * L'app si installa dal telefono: il manifest e' quello che il sistema legge per
 * mettere l'icona nella schermata iniziale e riaprire la pagina a schermo
 * intero. Passa da nginx, come lo chiedera' il telefono.
 */
test('il manifest e le icone arrivano, con il tipo giusto', async ({ page }) => {
  const manifest = await page.request.get('/manifest.webmanifest');
  expect(manifest.status()).toBe(200);
  expect(manifest.headers()['content-type']).toContain('application/manifest+json');
  const dati = await manifest.json() as { display: string; start_url: string; name: string; icons: { src: string; sizes: string }[] };
  expect(dati.display).toBe('standalone');
  expect(dati.start_url).toBe('/');
  expect(dati.name).not.toBe('');
  // Le due misure che il sistema chiede per l'icona: senza, non si installa.
  expect(dati.icons.map((icona) => icona.sizes)).toEqual(['192x192', '512x512']);
  // iOS l'icona non la prende dal manifest: la vuole dichiarata nella pagina.
  const pagina = await page.request.get('/');
  const html = await pagina.text();
  expect(html).toContain('apple-touch-icon.png');
  expect(html).toContain('theme-color');
  for (const icona of [...dati.icons.map((i) => i.src), '/apple-touch-icon.png']) {
    const risposta = await page.request.get(icona);
    expect(risposta.status(), icona).toBe(200);
    expect(risposta.headers()['content-type'], icona).toBe('image/png');
  }
});

test('a 390 px nessuna pagina scorre in orizzontale e nessun controllo resta tagliato', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('Dati aggiornati').first()).toBeAttached();
  const problemi: string[] = [];
  for (const voce of PAGINE) {
    await apri(page, voce);
    await page.waitForTimeout(400);
    if (process.env.MOBILE_SCREENSHOT) {
      await page.screenshot({ path: `${process.env.MOBILE_SCREENSHOT}/${voce.replace(/\W+/g, '-')}.png`, fullPage: true });
    }
    const larga = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
    const tagliati = await sporgenze(page);
    if (larga || tagliati.length) problemi.push(`${voce}: ${larga ? 'la pagina scorre in orizzontale; ' : ''}${tagliati.join(' | ')}`);
  }
  expect(problemi).toEqual([]);
});
