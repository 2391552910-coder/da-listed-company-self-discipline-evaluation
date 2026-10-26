"""GPU subprocess boundary: no web framework, ORM, credentials or research .env."""
from dataclasses import asdict
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

EMBEDDING_MODEL="Qwen/Qwen3-Embedding-0.6B"
EMBEDDING_REVISION="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
RERANKER_REVISION="e61197ed45024b0ed8a2d74b80b4d909f1255473"
QUERY_PROMPT="Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:"

def digest(data):
    return hashlib.sha256(data).hexdigest()

def read_dataset(directory,manifest=None):
    data=(directory/"dataset.json").read_bytes()
    if manifest and digest(data)!=manifest["dataset_sha256"]:
        raise ValueError("Dataset hash mismatch")
    return json.loads(data),digest(data)

def chunks_from(rows):
    from app.research_adapter.vendor.data.clause_splitter import Chunk
    return [Chunk(chunk_id=r["id"],doc_id=r["version_id"],doc_title=r["title"],doc_type=r["corpus_type"],
                  source="uploaded_version",clause_no=None,text=r["content"],
                  meta={"corpus_type":r["corpus_type"],"stock_code":r["stock_code"],"report_year":r["report_year"]})
            for r in rows]

def dense_kwargs():
    return dict(backend="qwen",model_name=EMBEDDING_MODEL,device="cuda",dtype="float16",batch_size=1,
                query_prompt=QUERY_PROMPT,max_seq_length=2048,revision=EMBEDDING_REVISION,local_files_only=True)

def build(directory):
    data,data_sha=read_dataset(directory)
    profile=data["config"]["profile"]
    manifest={"format":"dalun-source-index-v1","profile":profile,"dataset_sha256":data_sha,
              "chunk_count":len(data["rows"]),"encoder_identity":{"backend":"bm25_diagnostic"},
              "vector_sha256":None}
    if profile=="qwen_cuda":
        import numpy as np
        from app.research_adapter.vendor.retrieval.dense import DenseRetriever
        dense=DenseRetriever(chunks_from(data["rows"]),**dense_kwargs())
        matrix=np.asarray(dense._matrix,dtype=np.float32)
        if matrix.ndim!=2 or not np.isfinite(matrix).all():
            raise ValueError("Invalid vector matrix")
        np.save(directory/"vectors.npy",matrix,allow_pickle=False)
        manifest.update(encoder_identity=dense.encoding_metadata,vector_shape=list(matrix.shape),
                        vector_sha256=digest((directory/"vectors.npy").read_bytes()))
    elif profile!="bm25_diagnostic":
        raise ValueError("Unknown profile")
    destination=directory/"manifest.json"
    destination.write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True),encoding="utf-8")
    print(json.dumps({"status":"built","chunks":manifest["chunk_count"]}))

def query(directory,request_file,response_file,reference_root=None):
    started=time.monotonic()
    manifest=json.loads((directory/"manifest.json").read_bytes())
    if manifest["format"]!="dalun-source-index-v1":
        raise ValueError("Unsupported artifact")
    dataset,_=read_dataset(directory,manifest)
    request=json.loads(Path(request_file).read_bytes())
    chunks=chunks_from(dataset["rows"])
    retriever=None
    if manifest["profile"]=="qwen_cuda":
        import numpy as np
        from app.research_adapter.vendor.retrieval.hybrid import HybridRetriever
        from app.research_adapter.vendor.retrieval.reranker import Reranker
        if digest((directory/"vectors.npy").read_bytes())!=manifest["vector_sha256"]:
            raise ValueError("Vector hash mismatch")
        vectors=np.load(directory/"vectors.npy",allow_pickle=False)
        if list(vectors.shape)!=manifest["vector_shape"] or len(vectors)!=len(chunks) or vectors.dtype!=np.float32:
            raise ValueError("Vector shape/dtype mismatch")
        if not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors,axis=1),1,atol=.02):
            raise ValueError("Vectors must be finite and normalized")
        rr=Reranker(revision=RERANKER_REVISION,local_files_only=True) if dataset["config"]["rerank"] else None
        retriever=HybridRetriever(chunks,document_vectors=vectors,reranker=rr,vector_weight=.7,
                                   fusion="weighted",rerank_pool_factor=4,**dense_kwargs())
        # Frozen identity: requested model alone is insufficient.
        if retriever.dense.encoding_metadata!=manifest["encoder_identity"]:
            raise ValueError("Encoder identity mismatch")
    else:
        from app.research_adapter.vendor.retrieval.bm25 import BM25Retriever
        retriever=BM25Retriever(chunks)
    pools={"annual_report":[c for c in chunks if c.meta["corpus_type"]=="annual_report" and
                           c.meta["stock_code"]==request["stock_code"] and c.meta["report_year"]==request["report_year"]],
           "regulation":[c for c in chunks if c.meta["corpus_type"]=="regulation"]}
    if not pools["annual_report"]:
        raise ValueError("Empty annual scope")
    result={"annual_report":[],"regulation":[],"traces":{}}
    original=None
    if reference_root:
        sys.path.insert(0,str(Path(reference_root).resolve()))
        # Share already-loaded frozen encoder/weights: this tests ranking adapter parity, not independent model accuracy.
        from src.retrieval.hybrid import HybridRetriever as Original
        from src.retrieval.bm25 import BM25Retriever as OriginalBM25
        if manifest["profile"]=="qwen_cuda":
            original=Original.__new__(Original)
            original.__dict__.update(retriever.__dict__)
            original.bm25=OriginalBM25(chunks)
    parity=[]
    for role,pool in pools.items():
        if not pool:
            hits=[]
        elif manifest["profile"]=="qwen_cuda":
            from app.research_adapter.vendor.retrieval.search_trace import SearchTrace
            trace=SearchTrace(request["query"],request["top_k"],[c.chunk_id for c in pool],"dual_scoped_hybrid")
            hits=retriever.search(request["query"],request["top_k"],pool=pool,trace=trace)
            result["traces"][role]=trace.data
            if original:
                control=original.search(request["query"],request["top_k"],pool=pool)
                parity.append([(c.chunk_id,float(s)) for c,s in hits]==[(c.chunk_id,float(s)) for c,s in control])
        else:
            hits=retriever.search(request["query"],request["top_k"],candidate_ids={c.chunk_id for c in pool})
        result[role]=[{"chunk_id":c.chunk_id,"score":float(s)} for c,s in hits]
    result["elapsed_seconds"]=round(time.monotonic()-started,3)
    result["research_parity"]={"matched":all(parity),"pools_compared":len(parity),"shared_frozen_encoder":True} if parity else None
    Path(response_file).write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding="utf-8")

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=["build","query"])
    parser.add_argument("directory",type=Path)
    parser.add_argument("request_file",nargs="?")
    parser.add_argument("response_file",nargs="?")
    parser.add_argument("--reference-root")
    args=parser.parse_args()
    if args.command=="build":
        build(args.directory)
    else:
        query(args.directory,args.request_file,args.response_file,args.reference_root)

if __name__=="__main__":
    main()
