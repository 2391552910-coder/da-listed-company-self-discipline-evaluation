"""Versioned system questions; these are not a historical experimental dataset."""
from copy import deepcopy
from datetime import date
import re

from app.documents.common import DomainError


VERSION = "four-dim-v1"
DIMENSIONS = (
    {"code": "D1", "name": "信息披露质量"},
    {"code": "D2", "name": "董事会治理有效性"},
    {"code": "D3", "name": "内部控制与合规性"},
    {"code": "D4", "name": "第三方声誉"},
)
_QUESTIONS = (
    {"key": "D1-01", "dimension": "D1", "text": "依据年报与法规证据，评价公司信息披露的完整性、准确性与及时性；逐项区分已披露事实、法规要求及当前资料无法核实的事项。"},
    {"key": "D2-01", "dimension": "D2", "text": "依据年报与法规证据，分析董事会、独立董事及审计委员会履职披露，说明董事会治理有效性的可观察依据与评价限制。"},
    {"key": "D3-01", "dimension": "D3", "text": "依据年报与法规证据，分析内部控制制度、缺陷与整改披露及合规风险；不要将未披露事项视为不存在，也不要仅凭制度描述认定实际执行有效。"},
    {"key": "D4-01", "dimension": "D4", "text": "依据当前年报与法规证据，梳理可观察的第三方评价、监管记录及声誉相关信息，明确独立第三方材料的缺口；不得因缺少第三方资料推断声誉差或声誉良好。"},
)
_ALLOWED = {"key", "dimension", "text", "declared_target_date", "target_date_role"}


def _bad(message):
    raise DomainError("INVALID_QUESTIONS", message, 422)


def _text(value, name, minimum=1, maximum=4000):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        _bad(name + "须为长度合法的非空文本")
    try:
        value.encode("utf-8")
    except UnicodeError:
        _bad(name + "包含无效Unicode字符")
    return value.strip()


def normalize_questions(version=VERSION, questions=None):
    """Freeze a base template or an explicitly labelled custom question snapshot.

    Dates are user-declared event/evaluation dates. No annual-report year is
    substituted for a missing date, and metadata does not certify applicability.
    """
    if version != VERSION:
        raise DomainError("QUESTION_SET_NOT_FOUND", "问题集版本不存在", 404)
    custom = questions is not None
    values = questions if custom else deepcopy(_QUESTIONS)
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 24:
        _bad("问题列表须包含1至24题")
    result = []
    seen = set()
    for question in values:
        if not isinstance(question, dict) or set(question) - _ALLOWED:
            _bad("问题对象包含未支持字段")
        if not {"key", "dimension", "text"} <= set(question):
            _bad("每题须包含key、dimension与text")
        key = _text(question["key"], "key", maximum=64)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", key) or key in seen:
            _bad("问题标识须唯一且仅含字母、数字、下划线或短横线")
        seen.add(key)
        dimension = question["dimension"]
        if not isinstance(dimension, str) or dimension not in {item["code"] for item in DIMENSIONS}:
            _bad("问题维度须为D1、D2、D3或D4")
        text = _text(question["text"], "text")
        target = question.get("declared_target_date")
        role = question.get("target_date_role")
        if target is not None:
            if not isinstance(target, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", target):
                _bad("声明日期须为YYYY-MM-DD")
            try:
                date.fromisoformat(target)
            except ValueError:
                _bad("声明日期不是有效日期")
            if not isinstance(role, str) or role not in {"event_date", "evaluation_date"}:
                _bad("声明日期须指定event_date或evaluation_date角色")
        elif role is not None:
            _bad("未提供声明日期时不得指定日期角色")
        result.append({"key": key, "dimension": dimension, "text": text,
                       "declared_target_date": target, "target_date_role": role})
    return {"version": "custom" if custom else VERSION, "base_version": VERSION,
            "is_custom": custom, "questions": result}


def question_sets():
    snapshot = normalize_questions()
    return [{"version": VERSION, "name": "上市公司自律四维基础问题集",
             "description": "依据系统四维评价范围编制的基础模板；不冒称论文历史实验问题集。第三方资料缺失须明确披露。",
             "dimensions": deepcopy(list(DIMENSIONS)), "questions": snapshot["questions"]}]
