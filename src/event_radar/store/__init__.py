"""The radar's Postgres store: the connection and migrations, plus one repository per
aggregate, reached as `store.pages`, `store.sources` and so on."""

from .database import Database, now_iso
from .events import Events
from .extraction import Extraction
from .intakes import Intakes
from .judgments import Judgments
from .observations import Observations
from .organisers import Organisers
from .pages import Pages
from .runs import Runs
from .sources import Sources
from .users import Users

__all__ = ["Store", "now_iso"]


class Store(Database):
    def __init__(self, dsn: str | None, schema: str = "radar", api: str = "api"):
        super().__init__(dsn, schema, api)
        self.organisers = Organisers(self)
        self.sources = Sources(self)
        self.pages = Pages(self)
        self.observations = Observations(self)
        self.extraction = Extraction(self)
        self.judgments = Judgments(self)
        self.events = Events(self)
        self.users = Users(self)
        self.intakes = Intakes(self)
        self.runs = Runs(self)
