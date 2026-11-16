from sqlalchemy.orm import declarative_base

from backend.edge.app.core.config import settings
from backend.shared.db import make_engine, make_session_tools

engine = make_engine(settings.database_url)
SessionLocal, get_db, session_scope = make_session_tools(engine)
Base = declarative_base()
