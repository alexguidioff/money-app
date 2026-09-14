from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core_routes import notes
from app.database import Base
from app.models import Note


class NoteApiTests(unittest.TestCase):
    def test_non_espone_la_vecchia_cella_excel(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine, tables=[Note.__table__])
        with Session(engine) as session:
            session.add(Note(section="Appunti", title="Prova", source_cell="A1"))
            session.commit()
            voce = notes(session)["items"][0]
        self.assertNotIn("sourceCell", voce)


if __name__ == "__main__":
    unittest.main()
