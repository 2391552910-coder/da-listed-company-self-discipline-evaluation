"""Serializable candidate-stage traces and arithmetic replay, without qrels."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from datetime import datetime, timezone

SCHEMA = 'candidate-search-trace-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def score_value(value):
    try:
        number = float(value)
        if isinstance(value, bool) or not math.isfinite(number):
            raise ValueError('not a finite numeric score')
        return number
    except (TypeError, ValueError, OverflowError):
        return {'invalid_score_repr': repr(value)[:200]}


def hit_rows(hits):
    return [{'rank': rank, 'chunk_id': chunk.chunk_id, 'doc_id': chunk.doc_id,
             'doc_type': chunk.doc_type, 'text_sha256': hashlib.sha256(chunk.text.encode('utf-8')).hexdigest(),
             'metadata_sha256': digest(chunk.meta), 'score': score_value(score)}
            for rank, (chunk, score) in enumerate(hits, 1)]


class SearchTrace:
    def __init__(self, query, top_k, eligible_ids, pipeline_kind, sink=None):
        self.sink = sink
        self.data = {'schema_version': SCHEMA, 'trace_id': uuid.uuid4().hex, 'query': query,
            'requested_top_k': top_k, 'pipeline_kind': pipeline_kind,
            'eligible_pool_count': len(eligible_ids), 'eligible_pool_ids_sha256': digest(sorted(eligible_ids)),
            'status': 'running', 'stage': 'created', 'events': [],
            'dense_returned': [], 'bm25_returned': [], 'dense_candidates': [], 'bm25_candidates': [],
            'dense_excluded_scope_ids': [], 'bm25_excluded_scope_ids': [],
            'fused_candidates': [], 'rerank_prefix': [], 'rerank_inputs': [],
            'rerank_scores': [], 'reranked_candidates': [], 'final_rankings': [],
            'fusion_policy': None, 'rerank_enabled': False,
            'channel_lists_scope': 'all_returned_top_depth_candidates_not_full_corpus_rankings',
            'source_or_semantic_labels_accepted': False}

    def emit(self, stage, **values):
        self.data.update(values)
        self.data['stage'] = stage
        self.data['events'].append({'sequence': len(self.data['events']) + 1, 'stage': stage,
                                   'recorded_at_utc': datetime.now(timezone.utc).isoformat()})
        if self.sink is not None:
            self.sink(copy.deepcopy(self.data))

    def finish(self, hits):
        self.emit('returned', status='returned', final_rankings=hit_rows(hits))

    def fail(self, error):
        if self.data['status'] == 'failed':
            return
        self.emit('failed', status='failed', failed_phase=self.data['stage'],
                  error_type=type(error).__name__, error_message=str(error))


def _checked(rows):
    ids = [row['chunk_id'] for row in rows]
    if len(ids) != len(set(ids)) or [r['rank'] for r in rows] != list(range(1, len(rows) + 1)):
        raise ValueError('Candidate trace has duplicate IDs or invalid ranks')
    if any(type(r['score']) not in (int, float) or not math.isfinite(r['score']) for r in rows):
        raise ValueError('Candidate trace contains nonfinite or invalid scores')
    return rows


def _same(actual, expected, name):
    _checked(actual)
    if len(actual) != len(expected):
        raise ValueError('Trace replay count differs: ' + name)
    for left, right in zip(actual, expected):
        if any(left[k] != right[k] for k in ('rank', 'chunk_id', 'doc_id', 'doc_type', 'text_sha256', 'metadata_sha256')) or \
                not math.isclose(left['score'], right['score'], rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError('Trace replay ranking/score differs: ' + name)


def replay_trace(trace):
    """Replay only returned channel/fusion/rerank arithmetic, not model quality."""
    if trace.get('schema_version') != SCHEMA or trace.get('status') != 'returned':
        raise ValueError('Replay requires a completed candidate search trace')
    k = trace['requested_top_k']
    if type(k) is not int or k < 1:
        raise ValueError('Trace depth must be a positive integer')
    if trace['eligible_pool_count'] == 0:
        if any(trace[key] for key in ('dense_returned', 'bm25_returned', 'dense_candidates', 'bm25_candidates',
                'fused_candidates', 'rerank_prefix', 'rerank_inputs', 'rerank_scores',
                'reranked_candidates', 'final_rankings')):
            raise ValueError('Empty candidate pool cannot have returned stages')
        return {'status': 'empty_scope_replayed', 'semantic_quality_verified': False}
    kind = trace['pipeline_kind']
    if kind in ('bm25', 'dense'):
        channel = _checked(trace[kind + '_candidates'])
        _same(channel, _checked(trace[kind + '_returned']), 'single_channel_raw')
        if len(channel) > k: raise ValueError('Single-channel return exceeds requested depth')
        _same(trace['final_rankings'], channel[:k], 'single_channel_final')
        return {'status': 'single_channel_replayed', 'semantic_quality_verified': False}
    if kind != 'hybrid':
        raise ValueError('Unknown trace pipeline kind')
    dense, sparse = _checked(trace['dense_candidates']), _checked(trace['bm25_candidates'])
    for name, candidates in [('dense', dense), ('bm25', sparse)]:
        raw = _checked(trace[name + '_returned'])
        excluded = trace[name + '_excluded_scope_ids']
        if len(excluded) != len(set(excluded)) or not set(excluded) <= {r['chunk_id'] for r in raw}:
            raise ValueError('Invalid out-of-scope candidate removal record')
        expected = [{**row, 'rank': i} for i, row in enumerate(
            [r for r in raw if r['chunk_id'] not in excluded], 1)]
        _same(candidates, expected, name + '_raw_to_scoped')
    policy = trace['fusion_policy']
    if len(dense) > k * 2 or len(sparse) > k * 2:
        raise ValueError('Returned channel pool exceeds the declared search depth')
    refs = {}
    for row in dense + sparse:
        prior = refs.setdefault(row['chunk_id'], row)
        if any(prior[key] != row[key] for key in ('doc_id', 'doc_type', 'text_sha256', 'metadata_sha256')):
            raise ValueError('Candidate identity changed between channels')
    fused = {}
    if policy['fusion'] == 'rrf':
        for rows in (dense, sparse):
            for rank, row in enumerate(rows, 1):
                cid = row['chunk_id'];fused[cid] = fused.get(cid, 0.0) + 1 / (policy['rrf_k'] + rank)
    elif policy['fusion'] == 'weighted':
        for rows, weight in [(dense, policy['vector_weight']), (sparse, 1-policy['vector_weight'])]:
            maximum = max((row['score'] for row in rows), default=0.0) or 1.0
            for row in rows:
                cid = row['chunk_id'];fused[cid] = fused.get(cid, 0.0) + row['score'] / maximum * weight
    else:
        raise ValueError('Unknown trace fusion policy')
    def ranked(values):
        return [{**refs[cid], 'rank': i, 'score': float(score)} for i, (cid, score) in enumerate(values, 1)]
    fusion = ranked(sorted(fused.items(), key=lambda row: row[1], reverse=True))
    _same(trace['fused_candidates'], fusion, 'fusion')
    prefix = fusion[:k * policy['rerank_pool_factor']]
    _same(trace['rerank_prefix'], prefix, 'pre_rerank_prefix')
    if trace['rerank_enabled'] and prefix:
        _same(trace['rerank_inputs'], prefix, 'actual_rerank_inputs')
        scores = trace['rerank_scores']
        if len(scores) != len(prefix) or any(type(s) not in (int, float) or not math.isfinite(s) for s in scores):
            raise ValueError('Trace has invalid aligned reranker scores')
        output = ranked(sorted(zip([r['chunk_id'] for r in prefix], scores), key=lambda row: row[1], reverse=True))
        _same(trace['reranked_candidates'], output, 'reranked')
        expected = output[:k]
    else:
        if trace['rerank_inputs'] or trace['rerank_scores'] or trace['reranked_candidates']:
            raise ValueError('Disabled reranker cannot have model inputs or outputs')
        expected = fusion[:k]
    _same(trace['final_rankings'], expected, 'final')
    return {'status': 'hybrid_arithmetic_replayed', 'dense_candidates': len(dense),
        'bm25_candidates': len(sparse), 'fused_candidates': len(fusion),
        'actual_rerank_inputs': len(trace['rerank_inputs']), 'semantic_quality_verified': False}
