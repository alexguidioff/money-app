'use client';

import { useEffect, useRef, useState } from 'react';
import { Input } from '@/components/ui/input';

/**
 * Un campo numerico che si riesce a svuotare.
 *
 * Con `value={numero}` e `Number(e.target.value)`, cancellare l'ultima cifra
 * rimette subito lo zero nel campo: `Number('')` e' 0, il campo si ridisegna
 * con 0 dentro e per scrivere un numero nuovo bisogna prima infilare una cifra
 * davanti allo zero. Il numero che si vede mentre si digita non e' quello che
 * l'app tiene, e l'unico modo di correggere un campo e' non poterlo svuotare.
 *
 * Qui il testo che si sta scrivendo vive nel campo, e all'app va solo quello
 * che si riesce a leggere come numero: svuotare e' permesso, e il campo resta
 * vuoto finche' non si scrive qualcosa. `mio` ricorda l'ultimo numero passato
 * di fuori, cosi' un profilo appena caricato entra nel campo, ma il testo che
 * si sta scrivendo non viene riscritto a ogni tasto.
 *
 * Vuoto vale 0 all'app: e' il salvataggio a dire se quello zero va bene, e i
 * controlli che rifiutano lo zero (`!profile.birthYear`) continuano a valere.
 */
export function NumeroField({ value, onChange, className, ...resto }:
  { value: number; onChange: (numero: number) => void } &
  Omit<React.ComponentProps<typeof Input>, 'value' | 'onChange'>) {
  const [testo, setTesto] = useState(() => String(value));
  const mio = useRef(value);
  useEffect(() => {
    if (value !== mio.current) {
      mio.current = value;
      setTesto(String(value));
    }
  }, [value]);
  return <Input {...resto} type="number" className={className} value={testo}
    onChange={(evento) => {
      const scritto = evento.currentTarget.value;
      setTesto(scritto);
      const numero = scritto.trim() === '' ? 0 : Number(scritto);
      if (Number.isFinite(numero)) {
        mio.current = numero;
        onChange(numero);
      }
    }} />;
}
