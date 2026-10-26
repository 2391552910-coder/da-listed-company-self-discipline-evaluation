"""Private cold backups. API and Workers must be stopped before use.

The quiesced acknowledgement is an operator declaration, not a process lock.
No hot consistency is claimed between PostgreSQL and asset files. Restoration
only writes a new asset directory and a new SQLite file or an empty PostgreSQL
database. It never drops a database or deletes an existing directory.
"""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sqlite3
import subprocess
from urllib.parse import parse_qs, unquote, urlsplit

from app.documents.common import DomainError, dump, now, sha

BACKUP_SCHEMA = "dalun-cold-backup-v1"
MANIFEST = "backup-manifest.json"


def _error(code, message):
    raise DomainError(code, message, 409)


def _linked(path):
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _hash_file(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _relative(value):
    if (not isinstance(value, str) or not value or "\\" in value or
            PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive):
        _error("BACKUP_INVALID_PATH", "备份清单包含无效相对路径")
    parts = PurePosixPath(value).parts
    if (not parts or any(p in {".", ".."} or ":" in p or p.rstrip(" .") != p or
                         any(ord(c) < 32 for c in p) for p in parts) or
            "/".join(parts) != value):
        _error("BACKUP_INVALID_PATH", "备份清单路径越界或格式不合法")
    return value


def _safe_root(path, *, exists=True):
    original = Path(path).absolute()
    # Inspect every existing ancestor before resolve(), including Windows junctions.
    for component in (original, *original.parents):
        if _linked(component):
            _error("BACKUP_LINK_REJECTED", "备份和恢复路径不能经过符号链接或目录联接")
    result = original.resolve()
    if exists and not result.is_dir():
        _error("BACKUP_DIRECTORY_MISSING", "指定目录不存在")
    return result


def _sensitive_asset(name):
    value = name.casefold()
    return (value == ".env" or value.startswith(".env.") or
            value.startswith(("credentials", "id_rsa", "id_ed25519")) or
            value.endswith((".pem", ".key", ".p12", ".pfx")))


def _inventory(root):
    files = []
    for parent, dirs, names in os.walk(root, followlinks=False):
        parent = Path(parent)
        for name in dirs + names:
            candidate = parent / name
            if _linked(candidate):
                _error("BACKUP_LINK_REJECTED", "备份目录包含符号链接或目录联接")
            if _sensitive_asset(name):
                _error("BACKUP_CONFIG_IN_ASSETS", "资料目录包含配置或密钥文件，请先纠正数据目录设置")
        for name in names:
            path = parent / name
            if not path.is_file():
                _error("BACKUP_NON_REGULAR_FILE", "资料目录包含非普通文件")
            relative = _relative(path.relative_to(root).as_posix())
            files.append((relative, path))
    return sorted(files)


def _new_target(path):
    result = _safe_root(path, exists=False)
    if result.exists():
        _error("RESTORE_TARGET_EXISTS", "目标必须是尚不存在的新目录或文件")
    return result


def _sqlite_filename(url):
    if not url.startswith("sqlite:///"):
        return None
    value = Path(url[len("sqlite:///"):]).absolute()
    if any(_linked(p) for p in (value, *value.parents) if p.exists()):
        _error("BACKUP_LINK_REJECTED", "数据库路径不能经过链接")
    return value.resolve()


def _readonly_sqlite(path):
    if not path.is_file():
        _error("BACKUP_DATABASE_MISSING", "备份数据库不存在")
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)


def _assert_idle(connection):
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM t_job WHERE status IN ('queued','running')")
        if cursor.fetchone()[0]:
            _error("BACKUP_JOBS_ACTIVE", "仍存在排队或运行中的任务，请先停止入口并完成或取消任务")
        cursor.execute("SELECT revision FROM t_document_schema ORDER BY revision")
        return [row[0] for row in cursor.fetchall()]
    except DomainError:
        raise
    except Exception:
        _error("BACKUP_DATABASE_INVALID", "数据库缺少可识别的业务表或当前连接不可用")
    finally:
        cursor.close()


def _pg_environment(url):
    try:
        value = urlsplit(url.replace("postgresql+psycopg://", "postgresql://", 1))
        if value.scheme not in {"postgres", "postgresql"} or not value.hostname or not value.path.strip("/"):
            raise ValueError()
        query = parse_qs(value.query)
        if set(query) - {"sslmode"}:
            raise ValueError()
        env = dict(os.environ)
        env.update(PGHOST=value.hostname, PGPORT=str(value.port or 5432),
                   PGDATABASE=unquote(value.path.lstrip("/")), PGUSER=unquote(value.username or "postgres"),
                   PGPASSWORD=unquote(value.password or ""), PGCONNECT_TIMEOUT="15")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", env["PGDATABASE"]):
            raise ValueError()
        if query.get("sslmode"):
            sslmode = query["sslmode"][0]
            if sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
                raise ValueError()
            env["PGSSLMODE"] = sslmode
        return env
    except (ValueError, TypeError):
        _error("BACKUP_DATABASE_URL_INVALID", "仅支持明确的PostgreSQL连接配置及可选sslmode")


def _pg_connection(url):
    _pg_environment(url)  # Validate before importing a driver or connecting.
    try:
        import psycopg
        env = _pg_environment(url)
        return psycopg.connect(host=env["PGHOST"], port=env["PGPORT"], dbname=env["PGDATABASE"],
                               user=env["PGUSER"], password=env["PGPASSWORD"],
                               connect_timeout=15, **({"sslmode": env["PGSSLMODE"]} if "PGSSLMODE" in env else {}))
    except Exception:
        _error("BACKUP_DATABASE_UNAVAILABLE", "PostgreSQL连接不可用，请检查驱动、服务和配置")


def _run_pg(executable, arguments, url):
    try:
        subprocess.run([str(executable), *map(str, arguments)], check=True, env=_pg_environment(url),
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=3600, shell=False)
    except (OSError, subprocess.SubprocessError):
        _error("BACKUP_DATABASE_TOOL_FAILED", "PostgreSQL备份或恢复工具失败；未输出连接口令或服务端原始错误")


def _file_record(path, relative):
    return {"path": _relative(relative), "size_bytes": path.stat().st_size, "sha256": _hash_file(path)}


def create_backup(settings, destination, *, quiesced=False, pg_dump="pg_dump"):
    if quiesced is not True:
        _error("BACKUP_REQUIRES_QUIESCED", "请先停止API与Worker，并显式确认quiesced冷备份")
    assets = _safe_root(settings.data_root)
    target = _new_target(destination)
    if target.is_relative_to(assets) or assets.is_relative_to(target):
        _error("BACKUP_PATH_OVERLAP", "备份目标与资料目录不能重叠")
    source_files = _inventory(assets)
    sqlite_path = _sqlite_filename(settings.database_url)
    if sqlite_path and sqlite_path.is_relative_to(assets):
        _error("BACKUP_PATH_OVERLAP", "SQLite数据库文件须与资料目录分开存放")
    connection = _readonly_sqlite(sqlite_path) if sqlite_path else _pg_connection(settings.database_url)
    try:
        revisions = _assert_idle(connection)
        target.mkdir(parents=True, exist_ok=False)
        database_name = "database.sqlite" if sqlite_path else "database.dump"
        database_file = target / database_name
        if sqlite_path:
            with closing(sqlite3.connect(database_file)) as output:
                connection.backup(output)
                if output.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    _error("BACKUP_DATABASE_INVALID", "SQLite完整性检查失败")
        else:
            _run_pg(pg_dump, ["--format=custom", "--no-owner", "--no-privileges", "--file", database_file], settings.database_url)
        records = [_file_record(database_file, database_name)]
        for relative, source in source_files:
            destination_file = target / "assets" / relative
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            before = _hash_file(source)
            shutil.copyfile(source, destination_file)
            record = _file_record(destination_file, "assets/" + relative)
            if record["sha256"] != before or _hash_file(source) != before:
                _error("BACKUP_SOURCE_CHANGED", "备份期间资料发生变化，请重新停机后使用全新目录重试")
            records.append(record)
        if [name for name, _ in _inventory(assets)] != [name for name, _ in source_files]:
            _error("BACKUP_SOURCE_CHANGED", "备份期间资料目录发生变化")
        manifest = {"schema_version": BACKUP_SCHEMA, "created_at_utc": now(), "quiesced": True,
                    "database_profile": "sqlite_diagnostic" if sqlite_path else "postgresql",
                    "database_file": database_name, "schema_revisions": revisions,
                    "files": sorted(records, key=lambda row: row["path"])}
        manifest["manifest_sha256"] = sha(dump(manifest).encode())
        (target / MANIFEST).write_text(dump(manifest) + "\n", encoding="utf-8")
        verify_backup(target)
        return {"status": "succeeded", "database_profile": manifest["database_profile"],
                "file_count": len(records), "manifest_sha256": manifest["manifest_sha256"]}
    finally:
        connection.close()


def verify_backup(directory):
    root = _safe_root(directory)
    try:
        value = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
        expected = value.pop("manifest_sha256")
        if expected != sha(dump(value).encode()) or value["schema_version"] != BACKUP_SCHEMA or value.get("quiesced") is not True:
            raise ValueError()
        profile = value["database_profile"]
        database_file = "database.sqlite" if profile == "sqlite_diagnostic" else "database.dump" if profile == "postgresql" else None
        if value["database_file"] != database_file or not database_file or not isinstance(value["files"], list):
            raise ValueError()
        names = set()
        for record in value["files"]:
            name = _relative(record["path"])
            if name in names or not (name == database_file or name.startswith("assets/")):
                raise ValueError()
            if any(_sensitive_asset(part) for part in PurePosixPath(name).parts):
                raise ValueError()
            names.add(name)
            path = root / name
            for component in (path, *path.parents):
                if _linked(component):
                    _error("BACKUP_LINK_REJECTED", "备份包含链接，不能验证或恢复")
                if component == root:
                    break
            if (not path.is_file() or type(record["size_bytes"]) is not int or record["size_bytes"] < 0 or
                    path.stat().st_size != record["size_bytes"] or _hash_file(path) != record["sha256"]):
                _error("BACKUP_HASH_MISMATCH", "备份文件缺失、大小或哈希不匹配")
        if database_file not in names:
            raise ValueError()
        actual = {p.relative_to(root).as_posix() for _, p in _inventory(root)}
        if actual != names | {MANIFEST}:
            _error("BACKUP_UNEXPECTED_FILE", "备份目录含清单外文件，拒绝恢复")
        if profile == "sqlite_diagnostic":
            with closing(_readonly_sqlite(root / database_file)) as database:
                if database.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError()
        return value | {"manifest_sha256": expected}
    except DomainError:
        raise
    except (OSError, ValueError, KeyError, TypeError, sqlite3.DatabaseError):
        _error("BACKUP_MANIFEST_INVALID", "备份清单或数据库格式无效")


def restore_backup(directory, assets_destination, database_url, *, quiesced=False, pg_restore="pg_restore"):
    if quiesced is not True:
        _error("BACKUP_REQUIRES_QUIESCED", "恢复时请停止API与Worker并显式确认quiesced")
    root = _safe_root(directory)
    manifest = verify_backup(root)
    target = _new_target(assets_destination)
    if target.is_relative_to(root) or root.is_relative_to(target):
        _error("BACKUP_PATH_OVERLAP", "恢复目标与备份目录不能重叠")
    sqlite_target = _sqlite_filename(database_url)
    is_sqlite = manifest["database_profile"] == "sqlite_diagnostic"
    if is_sqlite != (sqlite_target is not None):
        _error("RESTORE_DATABASE_PROFILE_MISMATCH", "备份与恢复数据库类型不同")
    connection = None
    if sqlite_target:
        if sqlite_target.exists():
            _error("RESTORE_TARGET_EXISTS", "SQLite恢复目标必须是尚不存在的新文件")
        if (sqlite_target.is_relative_to(root) or sqlite_target.is_relative_to(target) or
                sqlite_target == target or target.is_relative_to(sqlite_target)):
            _error("BACKUP_PATH_OVERLAP", "恢复目标路径发生重叠")
    else:
        connection = _pg_connection(database_url)
        try:
            with connection.cursor() as cursor:
                for sql in (
                    "SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname !~ '^pg_' AND n.nspname!='information_schema'",
                    "SELECT COUNT(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname !~ '^pg_' AND n.nspname!='information_schema'",
                    "SELECT COUNT(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname !~ '^pg_' AND n.nspname!='information_schema'",
                    "SELECT COUNT(*) FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname NOT IN ('public','information_schema')",
                ):
                    cursor.execute(sql)
                    if cursor.fetchone()[0]:
                        _error("RESTORE_DATABASE_NOT_EMPTY", "PostgreSQL目标库必须预先创建且为空，不覆盖现有库")
        except DomainError:
            connection.close()
            raise
        except Exception:
            connection.close()
            _error("BACKUP_DATABASE_UNAVAILABLE", "无法确认PostgreSQL目标库为空")
        connection.close()
    target.mkdir(parents=True, exist_ok=False)
    # Failed restores leave only these newly created paths for inspection. No
    # existing path is removed and no destructive database command is issued.
    for record in manifest["files"]:
        if record["path"].startswith("assets/"):
            relative = record["path"][len("assets/"):]
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with (root / record["path"]).open("rb") as source, path.open("xb") as output:
                shutil.copyfileobj(source, output)
            if _hash_file(path) != record["sha256"]:
                _error("BACKUP_HASH_MISMATCH", "恢复文件校验失败，新目标保留用于排查")
    database_source = root / manifest["database_file"]
    if sqlite_target:
        sqlite_target.parent.mkdir(parents=True, exist_ok=True)
        with database_source.open("rb") as source, sqlite_target.open("xb") as output:
            shutil.copyfileobj(source, output)
        if _hash_file(sqlite_target) != _hash_file(database_source):
            _error("BACKUP_HASH_MISMATCH", "恢复数据库校验失败")
    else:
        _run_pg(pg_restore, ["--exit-on-error", "--single-transaction", "--no-owner", "--no-privileges", "--dbname", _pg_environment(database_url)["PGDATABASE"], database_source], database_url)
    return {"status": "succeeded", "database_profile": manifest["database_profile"],
            "file_count": len(manifest["files"]), "manifest_sha256": manifest["manifest_sha256"]}
