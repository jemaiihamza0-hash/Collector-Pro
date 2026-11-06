from .db import engine, transaction
from .models import Base, Clock


def initialize():
    Base.metadata.create_all(engine)
    with transaction() as db:
        if db.get(Clock, 1) is None:
            db.add(Clock(id=1, value=0))


if __name__ == "__main__":
    initialize()
    print("Base initialisée.")
