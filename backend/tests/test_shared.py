from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import User
from app.shared import andamento_condiviso


class AndamentoCondivisoTests(unittest.TestCase):
    def test_l_anno_in_corso_non_include_mesi_futuri(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine, tables=[User.__table__])
        with Session(engine) as session:
            persona = User(username="maria", display_name="Maria", shares_totals=True)
            session.add(persona)
            session.commit()
            oggi = date.today()
            punto = {"period": f"{oggi.year}-{oggi.month:02d}", "label": "oggi",
                     "netWorth": 1, "income": 1, "expenses": 0, "savings": 1}
            with patch("app.shared._serie_di", return_value=[punto]) as serie:
                andamento_condiviso(oggi.year, 0, 12, session)
            serie.assert_called_once_with(persona.id, oggi.year, oggi.month, 12)


if __name__ == "__main__":
    unittest.main()
