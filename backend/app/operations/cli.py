"""Private backup and public inventory commands; connection URLs never use argv."""
import argparse
import json
import os
from pathlib import Path

from app.documents.common import DomainError
from app.documents.settings import Settings
from app.operations.backup import create_backup, restore_backup, verify_backup
from app.operations.backup import _linked
from app.operations.manifest import build_manifest


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)
    backup.add_argument("--quiesced", action="store_true")
    backup.add_argument("--pg-dump", default="pg_dump")
    verify = commands.add_parser("verify-backup")
    verify.add_argument("directory", type=Path)
    restore = commands.add_parser("restore-backup")
    restore.add_argument("directory", type=Path)
    restore.add_argument("--assets-target", type=Path, required=True)
    restore.add_argument("--quiesced", action="store_true")
    restore.add_argument("--pg-restore", default="pg_restore")
    delivery = commands.add_parser("delivery-manifest")
    delivery.add_argument("root", type=Path)
    delivery.add_argument("--path", dest="paths", action="append")
    delivery.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "delivery-manifest":
            raw_output = args.output.absolute()
            if any(_linked(p) for p in (raw_output, *raw_output.parents)):
                raise DomainError("DELIVERY_LINK_REJECTED", "清单输出路径不能经过链接", 409)
            output = args.output.absolute().resolve()
            root = args.root.absolute().resolve()
            excluded = [output.relative_to(root).as_posix()] if output.is_relative_to(root) else []
            result = build_manifest(root, args.paths, exclude_paths=excluded)
            if output.exists():
                raise DomainError("DELIVERY_OUTPUT_EXISTS", "输出文件已存在，请选择新文件以保留历史清单", 409)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            result = {"status": "succeeded", "file_count": result["file_count"], "manifest_sha256": result["manifest_sha256"]}
        elif args.command == "verify-backup":
            value = verify_backup(args.directory)
            result = {"status": "verified", "file_count": len(value["files"]), "database_profile": value["database_profile"],
                      "manifest_sha256": value["manifest_sha256"]}
        else:
            settings = Settings.from_env()
            if args.command == "backup":
                result = create_backup(settings, args.destination, quiesced=args.quiesced, pg_dump=args.pg_dump)
            else:
                explicit_fields = all(os.getenv(name) for name in ("APP_DATABASE_HOST", "APP_DATABASE_NAME", "APP_DATABASE_USER"))
                if not os.getenv("APP_DATABASE_URL") and not explicit_fields:
                    raise DomainError("RESTORE_TARGET_REQUIRED", "请用环境变量或显式配置文件设置新目标数据库URL或完整HOST/NAME/USER字段", 422)
                result = restore_backup(args.directory, args.assets_target, settings.database_url,
                                        quiesced=args.quiesced, pg_restore=args.pg_restore)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except DomainError as error:
        print(json.dumps({"code": error.code, "message": str(error)}, ensure_ascii=False))
        return 1
    except Exception:
        # Do not serialize exception repr: drivers and external programs may put
        # passwords or connection strings into their diagnostic messages.
        print(json.dumps({"code": "OPERATIONS_FAILED", "message": "操作未完成；新建目标可能保留部分文件，请检查后使用全新目标重试"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
