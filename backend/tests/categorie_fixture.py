"""La categoria di un movimento in un test.

Da PIANO-B3 una categoria e' una riga, e movimenti, budget e regole la nominano
per id. Un test che scrive solo il nome lascia il movimento senza categoria: il
numero che sta controllando cambierebbe per un motivo che non c'entra niente
con quello che il test vuole provare, e il rosso arriverebbe dalla parte
sbagliata.

I nomi sono inventati: questo repository e' pubblico e i valori veri di chi usa
l'app non ci entrano.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Category


def categoria(session: Session, nome: str) -> int:
    """L'id della categoria con quel nome, creandola fra le radici.

    Cerca-e-crea invece di creare: lo stesso nome torna piu' volte nello stesso
    test, e due radici omonime non si possono avere. Il confronto e' senza
    maiuscole, come nel resto dell'app.
    """
    esistente = session.scalar(select(Category.id).where(Category.parent_id.is_(None),
                                                         func.lower(Category.name) == nome.casefold()))
    if esistente is not None:
        return esistente
    riga = Category(name=nome, parent_id=None)
    session.add(riga)
    session.flush()
    return riga.id
