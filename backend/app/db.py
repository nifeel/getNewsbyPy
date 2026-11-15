from sqlalchemy import inspect, text, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import BACKEND_DIR, settings
from app.models import Base

(BACKEND_DIR / "data").mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _ensure_article_title_zh()
    _ensure_event_time()


def _ensure_article_title_zh() -> None:
    inspector = inspect(engine)
    if "articles" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("articles")}
    if "title_zh" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE articles ADD COLUMN title_zh VARCHAR(512) NOT NULL DEFAULT ''"))


def _ensure_event_time() -> None:
    inspector = inspect(engine)
    if "events" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("events")}
    if "time" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE events ADD COLUMN time VARCHAR(16) NOT NULL DEFAULT ''"))


def get_session() -> Session:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
