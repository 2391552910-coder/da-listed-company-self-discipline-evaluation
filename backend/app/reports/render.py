"""Self-contained escaped HTML and searchable, embedded-CJK PDF.

The original canonical JSON is preserved with every export. PDF text is laid out
line by line across pages; there is no per-answer, per-source, or page-count cut.
"""
from html import escape
import json
import re

from app.documents.common import DomainError, dump, sha

RENDERER_VERSION = "evaluation-report-v1"
TITLE = "上市公司自律性评价报告"
NOTICE = ("本报告为基于冻结年报与法规证据的模型辅助文本评价。引用及逐字引文校验仅验证结构、位置与文本一致性，"
          "不等于语义支持、法律适用性或人工复核确认。第三章 AHP-Transformer 指数结果未接入，不生成数值评分。")
SOURCE_NOTICE = ("原件可通过证据版本 ID、物理页与字符区间回到系统核对。HTML 中的原件链接需在系统内预览打开；"
                 "离线报告不内嵌原始年报或法规文件。")
STATUSES = {
    "succeeded": "已完成", "partial": "部分完成", "failed": "失败", "insufficient": "证据不足",
    "sufficient": "模型声明证据充分", "budget_exhausted": "预算耗尽", "cancelled": "已取消",
    "queued": "排队中", "running": "运行中", "missing": "缺失", "not_evaluated": "未评价",
    "not_run": "未执行", "incomplete": "尚未完整完成",
    "not_reviewed": "尚未人工复核", "confirmed": "人工确认", "rejected": "人工驳回",
}


def value(item):
    """Preserve zero and distinguish an absent value from a measured zero."""
    if item is None:
        return "未提供 (null)"
    if isinstance(item, bool):
        return "true" if item else "false"
    if isinstance(item, (dict, list)):
        return json.dumps(item, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    return str(item)


def status(item):
    return STATUSES.get(item, value(item))


def limitations(items):
    if isinstance(items, list):
        return "\n".join("- " + value(item) for item in items) if items else "未登记额外限制"
    return value(items)


def _text(item):
    # Unsupported control characters are made visible rather than swallowed by
    # the HTML/PDF parsers. Their exact values remain in the canonical snapshot.
    return "".join(char if ord(char) >= 32 or char in "\n\t" else "\\u%04x" % ord(char)
                   for char in value(item))


def _source_link(source):
    version = source.get("version_id")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", version):
        return None
    page = source.get("physical_page")
    suffix = "#page=" + str(page) if type(page) is int and page > 0 else ""
    return "/api/v1/document-versions/" + version + "/file" + suffix


def blocks(evaluation, snapshot_sha256):
    """One semantic stream for HTML and PDF, with complete source appendices."""
    snapshot = evaluation.get("snapshot") or {}
    request = snapshot.get("request") or evaluation.get("request") or {}
    yield "title", TITLE
    yield "notice", NOTICE
    yield "body", SOURCE_NOTICE
    for label, item in (
        ("运行 ID", evaluation.get("id") or snapshot.get("run_id")),
        ("公司", evaluation.get("company_name") or evaluation.get("stock_code") or request.get("stock_code")),
        ("报告年度", evaluation.get("report_year") or request.get("report_year")),
        ("运行状态", status(evaluation.get("status"))),
        ("评价快照 SHA256", evaluation.get("snapshot_sha256")),
        ("导出冻结快照 SHA256", snapshot_sha256),
        ("第三章指数", "指数结果未接入"),
    ):
        yield "body", label + "：" + _text(item)
    summary = snapshot.get("summary") or {}
    yield "h1", "四维评价汇总"
    if summary.get("coverage_complete") is False:
        yield "notice", "本运行未覆盖四个维度。未执行维度：" + value(summary.get("not_run_dimensions")) + "。不能据此声称完成全维度评价。"
    if summary.get("question_set_is_custom"):
        yield "body", "本运行使用自定义问题，覆盖范围以冻结问题清单为准。"
    dimensions = summary.get("dimensions") or []
    if not dimensions:
        yield "body", "没有已生成的四维汇总。缺失值不记为零分。"
    for dimension in dimensions:
        yield "h2", _text(dimension.get("code")) + " " + _text(dimension.get("name"))
        yield "body", "状态：" + status(dimension.get("status"))
        yield "body", "；".join(label + "：" + value(dimension.get(key)) for label, key in (
            ("题目数", "question_count"), ("已完成题数", "succeeded_count"),
            ("失败题数", "failed_count"), ("证据不足题数", "evidence_insufficient_count")))
        if dimension.get("text") or dimension.get("conclusion"):
            yield "body", dimension.get("text") or dimension.get("conclusion")
    yield "body", "总体限制：\n" + limitations(summary.get("limitations"))
    answers = snapshot.get("answers") or []
    for number, answer in enumerate(answers, 1):
        yield "h1", "题目 " + str(number) + " · " + _text(answer.get("question_key"))
        yield "body", "所属维度：" + _text(answer.get("dimension"))
        yield "body", "问题：" + _text(answer.get("question_text"))
        yield "body", "执行状态：" + status(answer.get("status")) + "；证据状态：" + status(answer.get("evidence_status"))
        if answer.get("error_code"):
            yield "notice", "错误：" + _text(answer.get("error_code"))
        yield "body", answer.get("answer_text") if answer.get("answer_text") else "本题没有生成正文。"
        yield "body", "本题限制：\n" + limitations(answer.get("limitations"))
        yield "h2", "论断与引用"
        for claim_number, claim in enumerate(answer.get("claims") or [], 1):
            yield "body", "论断 " + str(claim_number) + "：" + _text(claim.get("text"))
            yield "body", "模型支持判断：" + _text(claim.get("support_status")) + "；人工复核：" + status(claim.get("human_review_status"))
            yield "body", "语义支持已独立核验：" + value(claim.get("semantic_support_verified", False))
            for citation in claim.get("citations") or []:
                yield "body", "[" + _text(citation.get("evidence_id")) + "] 引文：" + _text(citation.get("quote"))
                # Includes exact quote guard results, relationship and locator.
                yield "code", value(citation)
        yield "body", "机械校验结果（不代表语义认证）："
        yield "code", value(answer.get("validation"))
        context = answer.get("context") or {}
        yield "h2", "本题冻结证据附录"
        if not context.get("source_records"):
            yield "notice", "本题最终上下文无可引用证据。"
        for source in context.get("source_records") or []:
            yield "source", source
        yield "body", "上下文诊断："
        yield "code", value(context.get("diagnostics"))
        yield "body", "省略记录（完整保留）："
        yield "code", value(context.get("omitted_records"))
        yield "body", "检索与日期筛选记录："
        yield "code", value(answer.get("retrieval"))
    yield "h1", "冻结配置与调用记录"
    yield "body", "以下模型辅助结果未转写为第三章指数或人工评分。引用来源存在、引用文本吻合与语义支持分别记录。"
    yield "code", value(request)
    yield "code", value(snapshot.get("model_calls"))


def source_text(source):
    metadata = {key: item for key, item in source.items() if key != "content"}
    return "[" + _text(source.get("evidence_id")) + "] " + _text(source.get("title")) + "\n" + value(metadata) + "\n原文：\n" + _text(source.get("content"))


def render_html(evaluation, snapshot_bytes):
    fingerprint = sha(snapshot_bytes)
    parts = []
    for kind, text in blocks(evaluation, fingerprint):
        if kind == "source":
            href = _source_link(text)
            link = '<a href="' + escape(href, quote=True) + '" target="_blank" rel="noopener noreferrer">打开原始版本</a>' if href else "原始版本链接不可用"
            parts.append('<section class="evidence"><p>' + link + '</p><pre>' + escape(source_text(text)) + '</pre></section>')
        else:
            tag = {"title": "h1", "h1": "h2", "h2": "h3", "code": "pre"}.get(kind, "p")
            parts.append('<' + tag + ' class="' + kind + '">' + escape(_text(text)) + '</' + tag + '>')
    # The attachment is visible escaped text, never executable script or HTML.
    parts.append('<details><summary>完整冻结 JSON 快照</summary><pre>' + escape(snapshot_bytes.decode("utf-8")) + '</pre></details>')
    html = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'"><title>' + TITLE + '</title><style>' + CSS + '</style></head><body><main>' + "\n".join(parts) + '</main></body></html>'
    return html.encode("utf-8"), {"renderer": RENDERER_VERSION, "embedded_snapshot_sha256": fingerprint}


CSS = """
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f2f5f8;color:#183042;font-family:'Microsoft YaHei','Noto Sans CJK SC',sans-serif;font-size:15px;line-height:1.75}main{max-width:1000px;margin:32px auto;padding:44px;background:white;border-top:6px solid #166579}h1{font-size:27px}h2{font-size:22px;border-bottom:1px solid #dae4e9;padding-bottom:8px;margin-top:38px}h3{font-size:18px;color:#166579;margin-top:25px}p{white-space:pre-wrap;overflow-wrap:anywhere}pre{font-family:inherit;font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fa;padding:14px;border:1px solid #e3e9ed}.notice{border-left:4px solid #bd8531;padding:12px 16px;background:#fffaef}.evidence{border-left:3px solid #4b8c9a;padding-left:16px;margin:20px 0}a{color:#166579}details{margin-top:32px}summary{cursor:pointer}@media print{body{background:white}main{margin:0;padding:0;max-width:none}h1,h2,h3{break-after:avoid}.evidence{break-inside:auto}details{display:block}details>pre{display:block}a{color:inherit}}
"""


def render_pdf(evaluation, snapshot_bytes, font_file=None):
    import pymupdf as fitz

    try:
        font = fitz.Font(fontfile=str(font_file)) if font_file else fitz.Font("cjk")
    except Exception as error:
        raise DomainError("REPORT_FONT_UNAVAILABLE", "无法加载报告中文字体", 503) from error
    document = fitz.open()
    page, writer, baseline, page_number = None, None, 0, 0
    left, right, bottom = 43, 552, 789
    glyphs = {}

    def flush():
        if page is not None:
            writer.write_text(page)

    def new_page():
        nonlocal page, writer, baseline, page_number
        flush()
        page_number += 1
        page = document.new_page(width=595, height=842)
        page.draw_line((left, 39), (right, 39), color=(0.08, 0.4, 0.47), width=1)
        writer = fitz.TextWriter(page.rect)
        writer.append((left, 30), TITLE, font=font, fontsize=9)
        writer.append((left, 817), "冻结证据报告 · " + str(page_number), font=font, fontsize=8)
        baseline = 62

    def write_block(kind, text):
        nonlocal baseline
        text = _text(text).replace("\t", "    ")
        size = {"title": 22, "h1": 16, "h2": 12, "code": 8.4}.get(kind, 10.5)
        line_height = size * (1.55 if kind != "code" else 1.5)
        before = 10 if kind in {"h1", "h2"} else 3
        after = 7 if kind in {"h1", "title"} else 4
        if page is None or baseline + before + 2 * line_height > bottom:
            new_page()
        baseline += before
        lines = []
        for paragraph in text.split("\n"):
            line, width = "", 0.0
            for char in paragraph:
                if char not in glyphs:
                    glyphs[char] = font.has_glyph(ord(char)) or char.isspace()
                if not glyphs[char]:
                    raise DomainError("REPORT_GLYPH_UNAVAILABLE", "报告字体缺少字符 U+%04X，请配置包含该字符的字体" % ord(char), 422)
                char_width = font.text_length(char, fontsize=size)
                if line and width + char_width > right - left:
                    if char in "，。；：！？、）》】」』,.!?;:)]}" and len(line) > 1:
                        # Avoid an isolated closing punctuation mark at the
                        # start of the next Chinese line, without overflowing.
                        lines.append(line[:-1])
                        line = line[-1]
                        width = font.text_length(line, fontsize=size)
                    else:
                        lines.append(line)
                        line, width = "", 0.0
                line += char
                width += char_width
            lines.append(line)
        # Keep short paragraphs / JSON records intact when the next page has
        # room. Longer source text is deliberately split without truncation.
        block_height = len(lines) * line_height + after
        if len(lines) <= 16 and baseline + block_height > bottom and block_height < bottom - 62:
            new_page()
        for line in lines:
            if baseline > bottom:
                new_page()
            if line:
                writer.append((left, baseline), line, font=font, fontsize=size)
            baseline += line_height
        baseline += after

    try:
        for kind, text in blocks(evaluation, sha(snapshot_bytes)):
            if kind == "h1" and text == "冻结配置与调用记录" and page is not None:
                new_page()
            write_block("code" if kind == "source" else kind,
                        source_text(text) if kind == "source" else text)
        flush()
        document.set_metadata({"title": TITLE, "subject": "不可变证据、配置与辅助评价", "creator": RENDERER_VERSION})
        document.embfile_add("evaluation-snapshot.json", snapshot_bytes,
                             filename="evaluation-snapshot.json", desc="完整冻结评价 JSON，UTF-8")
        result = document.tobytes(garbage=4, deflate=True, no_new_id=True)
        return result, {"renderer": RENDERER_VERSION, "pymupdf_version": fitz.VersionBind,
                        "font_name": font.name, "font_sha256": sha(font.buffer),
                        "page_count": len(document), "embedded_snapshot_sha256": sha(snapshot_bytes)}
    finally:
        document.close()

