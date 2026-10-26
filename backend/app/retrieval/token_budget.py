"""Frozen reference tokenizer. Diagnostic bytes are never reported as provider tokens."""
from importlib.metadata import version
from pathlib import Path
from app.documents.common import DomainError,sha

REVISION="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
PINNED_SHA="def76fb086971c7867b829c23a26261e38d9d74e02139253b38aeb9df8b4b50a"

def load_counter(kind,settings,expected_identity=None):
    if kind=="utf8_diagnostic":
        identity={"kind":kind,"algorithm":"utf8-byte-count-v1","budget_unit":"utf8_bytes",
                  "is_provider_tokenizer":False,"is_model_tokenizer":False}
        counter=lambda text:len(text.encode("utf-8"))
    elif kind=="qwen3":
        path=Path(settings.reference_tokenizer_file) if settings.reference_tokenizer_file else (
            Path.home()/".cache"/"huggingface"/"hub"/"models--Qwen--Qwen3-Embedding-0.6B"/"snapshots"/REVISION/"tokenizer.json")
        if not path.is_file():
            raise DomainError("REFERENCE_TOKENIZER_UNAVAILABLE","参考分词器不可用，请显式配置文件并核对哈希",503)
        data=path.read_bytes()
        expected_sha=settings.reference_tokenizer_sha256 or PINNED_SHA
        if expected_sha!=PINNED_SHA:
            raise DomainError("TOKENIZER_IDENTITY_MISMATCH","Qwen3参考档只能使用已冻结的官方分词器哈希",409)
        if sha(data)!=expected_sha:
            raise DomainError("TOKENIZER_IDENTITY_MISMATCH","参考分词器哈希不匹配",409)
        try:
            from tokenizers import Tokenizer
        except ImportError:
            raise DomainError("REFERENCE_TOKENIZER_UNAVAILABLE","缺少tokenizers依赖",503)
        tokenizer=Tokenizer.from_str(data.decode("utf-8"))
        identity={"kind":kind,"model":"Qwen/Qwen3-Embedding-0.6B","revision":REVISION,
                  "tokenizer_sha256":sha(data),"library_version":version("tokenizers"),"add_special_tokens":False,
                  "budget_unit":"reference_tokens","is_provider_tokenizer":False,"is_model_tokenizer":True}
        counter=lambda text:len(tokenizer.encode(text,add_special_tokens=False).ids)
    else:
        raise DomainError("INVALID_TOKENIZER_KIND","不支持的参考分词器",422)
    if expected_identity is not None and identity!=expected_identity:
        raise DomainError("TOKENIZER_IDENTITY_MISMATCH","执行时的参考分词器与提交快照不同",409)
    return counter,identity
