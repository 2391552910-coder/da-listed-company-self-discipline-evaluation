"""Round-trip an isolated PostgreSQL/asset fixture; never touch a user database.

Requires an explicitly disposable TEST_POSTGRES_URL and PostgreSQL 16 tools.
Creates two uniquely named empty databases; the CI service owns their lifetime.
No DROP, DELETE or overwrite operation is performed.
"""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit,urlunsplit
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--pg-tools",type=Path,default=Path("/usr/lib/postgresql/16/bin"))
    args=parser.parse_args()
    from app.documents.common import sha
    from app.documents.settings import Settings
    from app.documents.service import Documents
    from app.documents.indexes import Indexes
    from app.documents.worker import work_once
    from app.operations.backup import create_backup,verify_backup,restore_backup
    import psycopg
    from psycopg import sql
    import fitz

    administrative=os.environ.get("TEST_POSTGRES_URL")
    if not administrative:
        raise RuntimeError("Disposable TEST_POSTGRES_URL is required; no default database is used")
    parsed=urlsplit(administrative)
    if parsed.scheme not in {"postgresql","postgresql+psycopg"}:
        raise RuntimeError("Disposable PostgreSQL connection is required")
    names=["dalun_backup_fixture_"+uuid4().hex[:12],"dalun_restore_fixture_"+uuid4().hex[:12]]
    connection=psycopg.connect(administrative.replace("postgresql+psycopg://","postgresql://",1),autocommit=True)
    with connection:
        for name in names:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    urls=[urlunsplit(parsed._replace(path="/"+name)) for name in names]
    root=Path(tempfile.mkdtemp(prefix="dalun-pg-backup-fixture-"))
    settings=replace(Settings.from_env(),database_url=urls[0],data_root=root/"source-assets",model_python=sys.executable)
    source=Documents(settings)
    source.db.migrate()
    company=source.company("019995","Synthetic backup fixture")
    original="合成备份验收规则，非真实法规。审计委员会监督内部控制与信息披露。".encode()
    document=source.upload(original,"synthetic-backup.txt","regulation","合成备份验收资料")
    pdf=fitz.open()
    page=pdf.new_page()
    page.insert_text((40,60),"Synthetic backup fixture 019995 2024年年度报告\n合成备份验收资料。审计委员会监督内部控制。",fontname="china-s",fontsize=12)
    annual_data=pdf.tobytes()
    pdf.close()
    annual=source.upload(annual_data,"synthetic-backup-annual.pdf","annual_report","合成备份验收年报",company["id"],2024)
    parse_ids=[]
    for version in (document,annual):
        source.enqueue_parse(version["id"])
        parsed_job=work_once(source)
        if parsed_job["status"]!="succeeded":
            raise RuntimeError("PG_BACKUP_FIXTURE_PARSE_FAILED:"+str(parsed_job.get("error_code")))
        parse_ids.append(parsed_job["result"]["parse_id"])
    Indexes(source).enqueue(parse_ids,"bm25_diagnostic",False)
    indexed=work_once(source)
    if indexed["status"]!="succeeded":
        raise RuntimeError("PG_BACKUP_FIXTURE_INDEX_FAILED:"+str(indexed.get("error_code")))
    chunk=source.chunks(document["id"])["items"][0]
    backup=create_backup(settings,root/"new-backup",quiesced=True,pg_dump=args.pg_tools/"pg_dump")
    manifest=verify_backup(root/"new-backup")
    restored=restore_backup(root/"new-backup",root/"new-restored-assets",urls[1],quiesced=True,pg_restore=args.pg_tools/"pg_restore")
    target=Documents(replace(settings,database_url=urls[1],data_root=root/"new-restored-assets"))
    assert target.db.ready()
    assert target.companies()[0]["stock_code"]=="019995"
    source_record=target.source(chunk["id"])
    assert source_record["source_available"] and source_record["file_sha256"]==sha(original)
    path,_=target.file(document["id"])
    assert path.read_bytes()==original
    annual_path,_=target.file(annual["id"])
    assert annual_path.read_bytes()==annual_data
    index,_,_,_=Indexes(target).validate(indexed["result"]["index_id"])
    assert index["status"]=="ready"
    summary={"schema_version":"postgresql-backup-acceptance-v1","status":"passed",
        "data_kind":"synthetic_fixture","database_profile":"PostgreSQL 16",
        "business_schema_revision":5,"database_and_assets_round_trip":True,
        "source_file_sha256":sha(original),"index_manifest_sha256":index["manifest_sha256"],
        "annual_file_sha256":sha(annual_data),"source_files_verified":2,
        "backup_manifest_sha256":manifest["manifest_sha256"],
        "restored_manifest_sha256":restored["manifest_sha256"],
        "file_count":len(manifest["files"]),"existing_databases_overwritten":0,
        "real_model_calls":0}
    assert backup["manifest_sha256"]==restored["manifest_sha256"]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8") as destination:
        json.dump(summary,destination,ensure_ascii=False,indent=2)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
