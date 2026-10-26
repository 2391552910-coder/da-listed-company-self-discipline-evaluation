import sys
from dataclasses import replace
import pytest
from app.documents.common import DomainError,sha
from app.documents.settings import Settings
from app.retrieval.token_budget import load_counter,PINNED_SHA

def settings(tmp_path,**kwargs):
    return replace(Settings("sqlite:///"+str(tmp_path/"d.sqlite"),tmp_path/"assets",sys.executable),**kwargs)

def test_diagnostic_count_is_explicitly_bytes(tmp_path):
    counter,identity=load_counter("utf8_diagnostic",settings(tmp_path))
    assert counter("中文")==6 and identity["budget_unit"]=="utf8_bytes"
    assert not identity["is_provider_tokenizer"] and not identity["is_model_tokenizer"]

def test_changed_counter_identity_rejected(tmp_path):
    with pytest.raises(DomainError,match="快照"):
        load_counter("utf8_diagnostic",settings(tmp_path),{"kind":"different"})

def test_custom_tokenizer_cannot_impersonate_frozen_qwen(tmp_path):
    path=tmp_path/"tokenizer.json"
    path.write_text("{}",encoding="utf-8")
    with pytest.raises(DomainError) as caught:
        load_counter("qwen3",settings(tmp_path,reference_tokenizer_file=str(path),reference_tokenizer_sha256=sha(path.read_bytes())))
    assert caught.value.code=="TOKENIZER_IDENTITY_MISMATCH"

def test_changed_tokenizer_file_rejected(tmp_path):
    path=tmp_path/"tokenizer.json"
    path.write_text("{}",encoding="utf-8")
    with pytest.raises(DomainError) as caught:
        load_counter("qwen3",settings(tmp_path,reference_tokenizer_file=str(path)))
    assert caught.value.code=="TOKENIZER_IDENTITY_MISMATCH"
