from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    url = url or get_settings().database_url
    kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30.0}} if url.startswith("sqlite") else {}
    return create_engine(url, future=True, pool_pre_ping=True, **kwargs)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401
    get_settings().data_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        if engine.url.drivername.startswith("sqlite"):
            conn.execute(text("PRAGMA journal_mode=WAL;"))
        inspector = inspect(conn)
        if "assessments" in inspector.get_table_names():
            columns = [col["name"] for col in inspector.get_columns("assessments")]
            if "allowed_emails" not in columns:
                conn.execute(text("ALTER TABLE assessments ADD COLUMN allowed_emails TEXT;"))
                conn.commit()
