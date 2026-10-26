from alembic import context
from sqlalchemy import create_engine
from app.documents.settings import Settings

def migrate():
    engine=create_engine(Settings.from_env().database_url)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    context.configure(url=Settings.from_env().database_url,literal_binds=True,dialect_opts={"paramstyle":"named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    migrate()
