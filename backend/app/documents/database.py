"""One SQL repository boundary. SQLite is an explicit offline diagnostic profile."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from app.documents.schema_v2 import schema_statements

class Session:
    def __init__(self, connection, dialect):
        self.connection, self.dialect = connection, dialect

    def execute(self, sql, params=()):
        cursor = self.connection.cursor()
        cursor.execute(sql if self.dialect == "sqlite" else sql.replace("?", "%s"), params)
        return cursor

    def rows(self, sql, params=()):
        cursor = self.execute(sql, params)
        names = [c[0] for c in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def one(self, sql, params=()):
        rows = self.rows(sql, params)
        return rows[0] if rows else None

class Database:
    def __init__(self, url):
        self.url = url
        self.dialect = "sqlite" if url.startswith("sqlite:///") else "postgresql"
        self.engine = None
        if self.dialect == "sqlite":
            self.filename = Path(url[len("sqlite:///"):]).resolve()
            self.filename.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def session(self):
        if self.dialect == "sqlite":
            connection = sqlite3.connect(self.filename, timeout=30)
            connection.execute("PRAGMA foreign_keys=ON")
        else:
            if self.engine is None:
                from sqlalchemy import create_engine
                self.engine = create_engine(self.url, pool_pre_ping=True)
            connection = self.engine.raw_connection()
        try:
            yield Session(connection, self.dialect)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self):
        with self.session() as session:
            for sql in schema_statements(self.dialect):
                session.execute(sql)
            session.execute("INSERT INTO t_document_schema(revision) VALUES(2) ON CONFLICT(revision) DO NOTHING")
            from app.documents.schema_v3 import upgrade_stage3
            upgrade_stage3(session)

    def ready(self):
        with self.session() as session:
            return session.one("SELECT revision FROM t_document_schema WHERE revision=3") is not None
