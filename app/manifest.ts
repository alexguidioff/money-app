import type { MetadataRoute } from 'next';

/**
 * Il manifest: e' quello che rende l'app installabile sul telefono - l'icona
 * sulla schermata iniziale e l'apertura a schermo intero, senza barra del
 * browser.
 *
 * Niente service worker, e non e' una dimenticanza: questa pagina mostra saldi e
 * movimenti, e una cache che serve numeri vecchi facendoli sembrare attuali e'
 * peggio di una schermata di errore. L'offline, se un giorno servira', e' una
 * decisione a se' con la sua discussione su cosa si puo' mostrare.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'Money — Le tue finanze',
    // Sul telefono l'etichetta sotto l'icona sta in poche lettere: il nome
    // intero verrebbe tagliato a meta'.
    short_name: 'Money',
    description: 'Gestione personale di conti, budget e movimenti.',
    start_url: '/',
    display: 'standalone',
    // I colori del tema predefinito: la barra di stato prende quello scuro del
    // menu, e mentre l'app si apre si vede lo sfondo delle pagine invece del
    // bianco del browser.
    theme_color: '#51461a',
    background_color: '#fafaf8',
    icons: [
      { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
      { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
    ],
  };
}
