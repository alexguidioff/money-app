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
