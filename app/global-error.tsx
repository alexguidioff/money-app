'use client';

import { ErrorFallback } from '@/components/error-fallback';
// Il layout non c'e' piu', quindi nemmeno il suo `import './globals.css'`: senza,
// questa pagina sarebbe testo grigio senza spaziature ne' colori.
import './globals.css';

/**
 * Il confine d'errore del layout: qui Next non ha piu' nessuno sopra, quindi la
 * pagina la scrivono queste righe, `<html>` e `<body>` compresi. La lingua e'
 * quella scritta nel layout - l'italiano - e la corregge il componente appena
 * sa quale hai scelto.
 */
export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="it">
      <body className="antialiased">
        <ErrorFallback onRetry={reset} />
      </body>
    </html>
  );
}
