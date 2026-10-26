"""Cold backup safety and real SQLite/assets round trips; no production DB calls."""
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from app.documents.common import DomainError, dump, sha
from app.documents.settings import Settings
from app.operations.backup import MANIFEST, create_backup, restore_backup, verify_backup


@pytest.fixture
def state(tmp_path):
    assets = tmp_path / "src"
    (assets / "nested").mkdir(parents=True)
    (assets / "nested" / "原文.txt").write_text("完整中文原文\n第二行\n", encoding="utf-8")
    (assets / "binary").write_bytes(bytes(range(256)))
    database = tmp_path / "source.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript("""CREATE TABLE t_job(id TEXT,status TEXT);
        CREATE TABLE t_document_schema(revision INTEGER);
        INSERT INTO t_document_schema VALUES(4);
        CREATE TABLE test_payload(name TEXT,score INTEGER);
        INSERT INTO test_payload VALUES('零分',0),('未计算',NULL);""")
    settings = Settings("sqlite:///" + str(database), assets, sys.executable)
    return settings


def backup_state(state, tmp_path):
    destination = tmp_path / "backup"
    summary = create_backup(state, destination, quiesced=True)
    return destination, summary


def reseal(path, mutate):
    value = json.loads((path / MANIFEST).read_text(encoding="utf-8"))
    value.pop("manifest_sha256")
    mutate(value)
    value["manifest_sha256"] = sha(dump(value).encode())
    (path / MANIFEST).write_text(dump(value), encoding="utf-8")


def test_sqlite_assets_restore_is_complete_and_immutable(state, tmp_path):
    destination, summary = backup_state(state, tmp_path)
    manifest = verify_backup(destination)
    assert manifest["schema_revisions"] == [4]
    assert summary["file_count"] == 3
    assert str(tmp_path) not in dump(manifest)
    target, database = tmp_path / "restored", tmp_path / "restored.sqlite"
    result = restore_backup(destination, target, "sqlite:///" + str(database), quiesced=True)
    assert result["manifest_sha256"] == summary["manifest_sha256"]
    assert (target / "nested" / "原文.txt").read_bytes() == (state.data_root / "nested" / "原文.txt").read_bytes()
    assert (target / "binary").read_bytes() == bytes(range(256))
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM test_payload ORDER BY rowid").fetchall() == [("零分", 0), ("未计算", None)]
    (state.data_root / "binary").write_bytes(b"later mutation")
    assert verify_backup(destination)["manifest_sha256"] == summary["manifest_sha256"]


@pytest.mark.parametrize("status", ["queued", "running"])
def test_active_jobs_reject_backup_before_creating_output(state, tmp_path, status):
    with sqlite3.connect(state.database_url[len("sqlite:///"):]) as connection:
        connection.execute("INSERT INTO t_job VALUES(?,?)", ("job", status))
    destination = tmp_path / "backup"
    with pytest.raises(DomainError) as error:
        create_backup(state, destination, quiesced=True)
    assert error.value.code == "BACKUP_JOBS_ACTIVE"
    assert not destination.exists()


def test_quiesced_declaration_is_required_for_both_directions(state, tmp_path):
    with pytest.raises(DomainError, match="quiesced"):
        create_backup(state, tmp_path / "backup")
    destination, _ = backup_state(state, tmp_path)
    with pytest.raises(DomainError, match="quiesced"):
        restore_backup(destination, tmp_path / "target", "sqlite:///" + str(tmp_path / "new.sqlite"))
    assert not (tmp_path / "target").exists()


def test_existing_assets_or_database_are_never_overwritten(state, tmp_path):
    destination, _ = backup_state(state, tmp_path)
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep").write_bytes(b"keep")
    with pytest.raises(DomainError) as error:
        restore_backup(destination, existing, "sqlite:///" + str(tmp_path / "new.sqlite"), quiesced=True)
    assert error.value.code == "RESTORE_TARGET_EXISTS"
    assert (existing / "keep").read_bytes() == b"keep"
    existing_db = tmp_path / "existing.sqlite"
    existing_db.write_bytes(b"must not overwrite")
    with pytest.raises(DomainError):
        restore_backup(destination, tmp_path / "new-assets", "sqlite:///" + str(existing_db), quiesced=True)
    assert existing_db.read_bytes() == b"must not overwrite"
    assert not (tmp_path / "new-assets").exists()


def test_corruption_is_rejected_before_restoration_writes(state, tmp_path):
    destination, _ = backup_state(state, tmp_path)
    (destination / "assets" / "binary").write_bytes(b"tampered")
    with pytest.raises(DomainError) as error:
        restore_backup(destination, tmp_path / "new", "sqlite:///" + str(tmp_path / "new.sqlite"), quiesced=True)
    assert error.value.code == "BACKUP_HASH_MISMATCH"
    assert not (tmp_path / "new").exists() and not (tmp_path / "new.sqlite").exists()


@pytest.mark.parametrize("path", ["../outside", "/absolute", "C:/outside", "assets/../../outside", "assets\\escape", "assets/a/./b", "assets/a:", "assets/end."])
def test_even_resealed_manifest_cannot_escape(state, tmp_path, path):
    destination, _ = backup_state(state, tmp_path)
    reseal(destination, lambda manifest: manifest["files"][0].update(path=path))
    with pytest.raises(DomainError):
        verify_backup(destination)
    assert not (tmp_path / "outside").exists()


def test_unlisted_file_and_manifest_mutation_are_rejected(state, tmp_path):
    destination, _ = backup_state(state, tmp_path)
    (destination / "extra").write_bytes(b"unlisted")
    with pytest.raises(DomainError) as error:
        verify_backup(destination)
    assert error.value.code == "BACKUP_UNEXPECTED_FILE"


def test_backup_targets_cannot_overlap_assets_or_capture_env(state, tmp_path):
    with pytest.raises(DomainError) as error:
        create_backup(state, state.data_root / "backup", quiesced=True)
    assert error.value.code == "BACKUP_PATH_OVERLAP"
    (state.data_root / ".env").write_text("LLM_API_KEY=private", encoding="utf-8")
    with pytest.raises(DomainError) as error:
        create_backup(state, tmp_path / "backup", quiesced=True)
    assert error.value.code == "BACKUP_CONFIG_IN_ASSETS"
    assert not (tmp_path / "backup").exists()


def test_database_inside_assets_is_rejected(state, tmp_path):
    inside = state.data_root / "source.sqlite"
    inside.write_bytes(Path(state.database_url[len("sqlite:///"):]).read_bytes())
    with pytest.raises(DomainError) as error:
        create_backup(replace(state, database_url="sqlite:///" + str(inside)), tmp_path / "backup", quiesced=True)
    assert error.value.code == "BACKUP_PATH_OVERLAP"


def test_source_links_are_never_followed(state, tmp_path):
    link = state.data_root / "link"
    try:
        link.symlink_to(tmp_path / "outside")
    except OSError:
        pytest.skip("Symlink creation unavailable for this user")
    with pytest.raises(DomainError) as error:
        create_backup(state, tmp_path / "backup", quiesced=True)
    assert error.value.code == "BACKUP_LINK_REJECTED"


class FakeCursor:
    def __init__(self, nonempty=False):
        self.sql = ""
        self.nonempty = nonempty
    def execute(self, sql):
        self.sql = sql
    def fetchone(self):
        return (1 if self.nonempty and "pg_class" in self.sql else 0,)
    def fetchall(self):
        return [(4,)]
    def close(self):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass


class FakeConnection:
    def __init__(self, nonempty=False):
        self.nonempty = nonempty
    def cursor(self):
        return FakeCursor(self.nonempty)
    def close(self):
        pass


def test_postgres_tools_use_environment_and_never_destructive_flags(state, tmp_path, monkeypatch):
    import app.operations.backup as module
    recorded = []
    def tool(command, **kwargs):
        recorded.append((command, kwargs))
        if "--file" in command:
            Path(command[command.index("--file") + 1]).write_bytes(b"explicit test dump fixture")
    monkeypatch.setattr(module, "_pg_connection", lambda _: FakeConnection())
    monkeypatch.setattr(module.subprocess, "run", tool)
    source = replace(state, database_url="postgresql+psycopg://user:private-password@localhost/source")
    destination = tmp_path / "backup"
    create_backup(source, destination, quiesced=True)
    restore_backup(destination, tmp_path / "new", "postgresql+psycopg://user:private-password@localhost/new_db", quiesced=True)
    assert len(recorded) == 2
    for command, options in recorded:
        assert "private-password" not in repr(command)
        assert options["env"]["PGPASSWORD"] == "private-password"
        assert options["shell"] is False
        assert "--clean" not in command and "--create" not in command
    assert "--single-transaction" in recorded[-1][0]


def test_postgres_existing_objects_reject_restore(state, tmp_path, monkeypatch):
    import app.operations.backup as module
    monkeypatch.setattr(module, "_pg_connection", lambda _: FakeConnection())
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs: Path(command[command.index("--file") + 1]).write_bytes(b"fixture"))
    destination = tmp_path / "backup"
    create_backup(replace(state, database_url="postgresql://user:secret@localhost/source"), destination, quiesced=True)
    monkeypatch.setattr(module, "_pg_connection", lambda _: FakeConnection(True))
    with pytest.raises(DomainError) as error:
        restore_backup(destination, tmp_path / "new", "postgresql://user:secret@localhost/existing", quiesced=True)
    assert error.value.code == "RESTORE_DATABASE_NOT_EMPTY"
    assert not (tmp_path / "new").exists()


def test_external_tool_error_does_not_leak_credentials(state, tmp_path, monkeypatch):
    import app.operations.backup as module
    monkeypatch.setattr(module, "_pg_connection", lambda _: FakeConnection())
    def failure(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr=b"password=private-password")
    monkeypatch.setattr(module.subprocess, "run", failure)
    with pytest.raises(DomainError) as error:
        create_backup(replace(state, database_url="postgresql://u:private-password@localhost/source"), tmp_path / "backup", quiesced=True)
    assert error.value.code == "BACKUP_DATABASE_TOOL_FAILED"
    assert "private-password" not in str(error.value)


def test_restore_cli_requires_explicit_database_target(tmp_path, monkeypatch, capsys):
    from app.operations.cli import main
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.delenv("DALUN_ENV_FILE", raising=False)
    for name in ("APP_DATABASE_HOST", "APP_DATABASE_NAME", "APP_DATABASE_USER"):
        monkeypatch.delenv(name, raising=False)
    assert main(["restore-backup", str(tmp_path / "absent"), "--assets-target", str(tmp_path / "new"), "--quiesced"]) == 1
    assert "RESTORE_TARGET_REQUIRED" in capsys.readouterr().out


def test_restore_cli_accepts_explicit_database_fields_without_printing_password(tmp_path, monkeypatch, capsys):
    import app.operations.cli as module
    monkeypatch.delenv("DALUN_ENV_FILE", raising=False)
    monkeypatch.setenv("APP_DATABASE_URL", "")
    monkeypatch.setenv("APP_DATABASE_HOST", "localhost")
    monkeypatch.setenv("APP_DATABASE_NAME", "explicit_new_target")
    monkeypatch.setenv("APP_DATABASE_USER", "fixture_user")
    monkeypatch.setenv("APP_DATABASE_PASSWORD", "private-password")
    called = []
    monkeypatch.setattr(module, "restore_backup", lambda *args, **kwargs: called.append(args) or {"status": "succeeded"})
    assert module.main(["restore-backup", str(tmp_path / "source"), "--assets-target", str(tmp_path / "new"), "--quiesced"]) == 0
    assert called
    assert "private-password" not in capsys.readouterr().out
