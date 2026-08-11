from app.config import get_settings
from app.reference import seed_reference_data
from app.repositories.database import create_database_engine, create_session_factory


def main() -> None:
    engine = create_database_engine(get_settings())
    factory = create_session_factory(engine)
    with factory.begin() as session:
        seed_reference_data(session)
    engine.dispose()


if __name__ == "__main__":
    main()
