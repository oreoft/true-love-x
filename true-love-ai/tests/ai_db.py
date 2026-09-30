"""An in-memory AI database, so tests never touch dbs/ai_data.db."""

from contextlib import ExitStack
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from true_love_ai.core import model_registry
from true_love_ai.core.db_engine import Base
from true_love_ai.memory import persona_service, skill_permission_service
from true_love_ai.models import model_setting, persona, skill_permission  # noqa: F401  registers the tables


def memory_db(test) -> sessionmaker:
    """Point every settings module at a fresh in-memory database for this test."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    stack = ExitStack()
    for module in (persona_service, skill_permission_service, model_registry):
        stack.enter_context(patch.object(module, "SessionLocal", factory))
    skill_permission_service._clear_cache()
    test.addCleanup(stack.close)
    test.addCleanup(skill_permission_service._clear_cache)
    test.addCleanup(engine.dispose)
    return factory
