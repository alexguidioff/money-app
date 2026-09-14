import { expect, test, type Page } from '@playwright/test';

/*
 * Pochi flussi, quelli che si sono rotti davvero: pagine che restavano bianche,
 * moduli che non salvavano, pannelli che non comparivano. Tutto sullo stack di
 * test; ogni test lascia i dati coerenti per il successivo.
 */

const PAGINE = ['Panoramica', 'Budget', 'Movimenti', 'Obiettivi', 'Patrimonio', 'Debiti', 'Investimenti',
  'Insieme', 'Appunti', 'Pensionamento e FIRE', 'Report', 'Impostazioni'];

function raccogliErrori(page: Page): string[] {
  const errori: string[] = [];
  page.on('pageerror', (errore) => errori.push(`pagina: ${errore.message}`));
  page.on('console', (messaggio) => { if (messaggio.type() === 'error') errori.push(`console: ${messaggio.text()}`); });
  return errori;
}

async function apri(page: Page, voce: string) {
  // Il menu laterale: le sezioni stanno nel <nav>, Impostazioni sotto, fuori.
  await page.locator('aside').getByRole('button', { name: voce, exact: true }).click();
}

async function avvia(page: Page) {
  await page.goto('/');
  await expect(page.getByText('Dati aggiornati')).toBeVisible();
}

test('ogni pagina del menu si apre senza errori', async ({ page }) => {
  const errori = raccogliErrori(page);
  await avvia(page);
  for (const voce of PAGINE) {
    await apri(page, voce);
    await expect(page.getByRole('heading', { level: 1 }).first(), voce).toBeVisible();
    await expect(page.getByText('Anteprima locale'), voce).toHaveCount(0);
  }
  expect(errori).toEqual([]);
});

test('FIRE: profilo salvato, flusso aggiunto, la pagina mostra il piano', async ({ page }) => {
  const errori = raccogliErrori(page);
  await avvia(page);
  await apri(page, 'Impostazioni');
  await page.getByLabel('Anno di nascita').fill('1998');
  await page.getByRole('button', { name: 'Salva profilo' }).click();
  await expect(page.getByText('Profilo salvato.')).toBeVisible();

  await page.getByRole('button', { name: 'Aggiungi flusso' }).click();
  await page.getByLabel('Nome', { exact: true }).fill('AVS');
  await page.getByLabel('Importo annuo').fill('20000');
  await page.getByRole('button', { name: 'Salva flusso' }).click();
  await expect(page.getByText('Flusso salvato.')).toBeVisible();

  await apri(page, 'Pensionamento e FIRE');
  await expect(page.getByText('Capitale necessario').first()).toBeVisible();
  expect(errori).toEqual([]);
});

test('Movimenti: una spesa si salva; un trasferimento al broker mostra il collegamento al ledger', async ({ page }) => {
  const errori = raccogliErrori(page);
  await avvia(page);
  await apri(page, 'Movimenti');
  await page.getByRole('button', { name: 'Nuovo movimento' }).first().click();
  const dialogo = page.getByRole('dialog');
  // Gli id dei campi: il nome accessibile di una tendina include l'opzione scelta.
  await dialogo.locator('#movement-amount').fill('12.50');
  await dialogo.locator('#movement-account').selectOption('Banca');
  await dialogo.locator('#movement-details').fill('Spesa e2e');
  await dialogo.getByRole('button', { name: 'Salva movimento' }).click();
  await expect(dialogo).toBeHidden();
  await expect(page.getByText('Spesa e2e').first()).toBeVisible();

  await page.getByRole('button', { name: 'Modifica Versamento broker e2e' }).click();
  await dialogo.locator('#movement-type').selectOption('Investment');
  await expect(dialogo.getByText('Operazioni collegate')).toBeVisible();
  await expect(dialogo.getByText(/viene salvato come Investimento/)).toBeVisible();
  await dialogo.getByRole('button', { name: 'Annulla' }).click();

  // Il tipo scelto nel movimento precedente non deve decidere le categorie del
  // successivo: aprendo la spesa, restavano quelle (nessuna) dell'investimento.
  await page.getByRole('button', { name: 'Modifica Spesa e2e' }).click();
  await expect(dialogo.locator('#movement-type')).toHaveValue('Expenses');
  await expect(dialogo.locator('#movement-category')).toBeEnabled();
  await expect(dialogo.locator('#movement-category option', { hasText: 'Groceries' })).toHaveCount(1);
  await dialogo.getByRole('button', { name: 'Annulla' }).click();

  // Registrando anche nel ledger, lo strumento si sceglie fra quelli che ci sono.
  await page.getByRole('button', { name: 'Nuovo movimento' }).first().click();
  await dialogo.locator('#movement-type').selectOption('Investment');
  await dialogo.locator('#movement-account').selectOption('Banca');
  await dialogo.getByLabel('Registra anche nel ledger').check();
  const strumento = dialogo.locator('input[list="strumenti-esistenti"]');
  await expect(strumento).toBeVisible();
  await expect(dialogo.locator('#strumenti-esistenti option[value="ETF e2e"]')).toHaveCount(1);
  await strumento.fill('ETF e2e');
  await dialogo.getByRole('button', { name: 'Annulla' }).click();
  expect(errori).toEqual([]);
});

test('Debiti: una linea di credito si configura e si salva', async ({ page }) => {
  // Il modulo di una linea non ha i campi del piano: arrivavano come la stringa
  // "null" e il salvataggio veniva rifiutato.
  const errori = raccogliErrori(page);
  await avvia(page);
  await apri(page, 'Debiti');
  await page.getByRole('button', { name: 'Configura debito' }).click();
  const dialogo = page.getByRole('dialog');
  await dialogo.locator('select[name="kind"]').selectOption('credit_line');
  await dialogo.locator('input[name="credit_limit"]').fill('30000');
  await dialogo.locator('input[name="annual_rate"]').fill('1.5');
  await dialogo.getByRole('button', { name: 'Salva', exact: true }).click();
  await expect(dialogo).toBeHidden();
  await expect(page.getByText('Impossibile salvare le condizioni del debito.')).toHaveCount(0);
  await expect(page.getByText('Linee di credito')).toBeVisible();
  expect(errori).toEqual([]);
});

test('Impostazioni: una preferenza resta dopo aver ricaricato', async ({ page }) => {
  const errori = raccogliErrori(page);
  await avvia(page);
  await apri(page, 'Impostazioni');
  const colore = page.getByLabel('Colore principale');
  await colore.selectOption('Green');
  await expect(page.getByText('Salvataggio…')).toHaveCount(0);
  await page.reload();
  await expect(page.getByText('Dati aggiornati')).toBeVisible();
  await apri(page, 'Impostazioni');
  await expect(page.getByLabel('Colore principale')).toHaveValue('Green');
  expect(errori).toEqual([]);
});
