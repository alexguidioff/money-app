import { expect, test, type Page } from '@playwright/test';

/*
 * Gli eventi, dal modulo fino alla card: un evento si crea mentre si salva la
 * prima spesa che gli appartiene, la seconda si aggancia dalla tendina, e i
 * totali si leggono in fondo a Movimenti.
 *
 * E' il giro che i test di contratto non possono fare: li' l'evento esiste gia'
 * nel seme, qui nasce da quello che l'utente scrive nel modulo, e la pastiglia
 * nella lista dipende da un aggancio che avviene dopo il salvataggio.
 */

async function apri(page: Page, voce: string) {
  await page.locator('aside').getByRole('button', { name: voce, exact: true }).click();
}

async function avvia(page: Page) {
  await page.goto('/');
  await expect(page.getByText('Dati aggiornati')).toBeVisible();
}

/** La riga della lista che contiene il movimento, per leggerne la pastiglia. */
function riga(page: Page, descrizione: string) {
  return page.getByRole('button', { name: `Modifica ${descrizione}` })
    .locator('xpath=ancestor::div[contains(@class, "flex-wrap")][1]');
}

test('un evento creato dal modulo raccoglie due spese e i totali si vedono nella card', async ({ page }) => {
  await avvia(page);
  await apri(page, 'Movimenti');
  await expect(page.getByText('Versamento broker e2e')).toBeVisible();

  // 1. La prima spesa crea l'evento: si sceglie "＋ nuovo evento…" e si scrive
  //    il nome li' dentro, senza uscire dal movimento che si sta salvando.
  const modulo = page.getByRole('dialog');
  await page.getByRole('button', { name: 'Nuovo movimento' }).click();
  await modulo.getByLabel('Descrizione').fill('Cena e2e');
  await modulo.getByLabel('Importo').fill('40');
  await modulo.getByLabel('Categoria').selectOption('Groceries');
  // Per nome e non per etichetta: la tendina del conto di destinazione comincia
  // con le stesse parole, e il nome accessibile si porta dietro la voce scelta
  // ("ContoNessun"), quindi nemmeno l'etichetta esatta lo distingue.
  await modulo.locator('[name="account_name"]').selectOption('Banca');
  const evento = modulo.getByLabel('Evento');
  await expect(evento.locator('option')).toHaveText(['Nessuno', '＋ nuovo evento…']);
  await evento.selectOption('__nuovo__');
  await modulo.getByLabel("Nome dell'evento").fill('Viaggio e2e');
  await modulo.getByRole('button', { name: 'Salva movimento' }).click();
  await expect(modulo).toBeHidden();

  // 2. La seconda si aggancia dalla tendina, che ora offre l'evento appena
  //    creato: e' l'evento a essere cambiato, non il modo di salvare.
  await page.getByRole('button', { name: 'Nuovo movimento' }).click();
  await modulo.getByLabel('Descrizione').fill('Treno e2e');
  await modulo.getByLabel('Importo').fill('60');
  await modulo.getByLabel('Categoria').selectOption('Housing');
  // Per nome e non per etichetta: la tendina del conto di destinazione comincia
  // con le stesse parole, e il nome accessibile si porta dietro la voce scelta
  // ("ContoNessun"), quindi nemmeno l'etichetta esatta lo distingue.
  await modulo.locator('[name="account_name"]').selectOption('Banca');
  await expect(modulo.getByLabel('Evento').locator('option')).toHaveText(['Nessuno', 'Viaggio e2e', '＋ nuovo evento…']);
  await modulo.getByLabel('Evento').selectOption({ label: 'Viaggio e2e' });
  await modulo.getByRole('button', { name: 'Salva movimento' }).click();
  await expect(modulo).toBeHidden();

  // Nella lista ognuno dei due porta il nome dell'evento.
  await expect(riga(page, 'Cena e2e')).toContainText('Viaggio e2e');
  await expect(riga(page, 'Treno e2e')).toContainText('Viaggio e2e');

  // 3. La card in fondo: un evento, due movimenti, quello che e' costato.
  const rigaEvento = page.getByRole('button', { name: /Viaggio e2e/ });
  await expect(rigaEvento).toContainText('2 movimenti');
  await expect(rigaEvento).toContainText('100,00');
  await expect(rigaEvento).toContainText('Senza date');

  // Aprendola si vedono i movimenti e la ripartizione per categoria: due
  // categorie diverse, cosi' il taglio trasversale si vede davvero.
  await rigaEvento.click();
  await expect(page.getByText('Per categoria')).toBeVisible();
  const ripartizione = page.getByText('Per categoria').locator('xpath=following-sibling::div[1]');
  await expect(ripartizione).toContainText('Groceries');
  await expect(ripartizione).toContainText('40,00');
  await expect(ripartizione).toContainText('Housing');
  await expect(ripartizione).toContainText('60,00');
  // La riga del movimento compare due volte: nella lista e dentro l'evento.
  await expect(page.getByText('Treno e2e')).toHaveCount(2);

  // 4. Il filtro per evento in cima alla lista lascia solo i suoi movimenti.
  //    `.first()` perche' l'evento e' ancora aperto nella card, e ogni
  //    descrizione compare due volte: nella lista e dentro il dettaglio.
  await page.getByLabel('Evento').selectOption({ label: 'Viaggio e2e' });
  await expect(page.getByText('Versamento broker e2e')).toHaveCount(0);
  await expect(page.getByText('Cena e2e').first()).toBeVisible();
  await expect(page.getByText('Treno e2e').first()).toBeVisible();
});
