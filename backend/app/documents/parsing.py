"""Deterministic source frames. No OCR text is invented for image-only pages."""
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile
import re
from app.documents.common import DomainError, sha

DEFAULT_CONFIG = {"chunk_chars": 1200, "overlap_chars": 160, "physical_pages": None}

def parse_file(data, file_format, config, checkpoint=None):
    size, overlap = config["chunk_chars"], config["overlap_chars"]
    if not 100 <= size <= 8000 or not 0 <= overlap < size:
        raise DomainError("INVALID_CHUNK_CONFIG", "分块大小或重叠参数不合法", 422)
    frames, warnings = [], []
    if file_format == "pdf":
        import fitz
        with fitz.open(stream=data, filetype="pdf") as document:
            if document.needs_pass:
                raise DomainError("ENCRYPTED_PDF", "加密PDF不能直接解析")
            selected = config.get("physical_pages") or list(range(1, len(document) + 1))
            if not selected or min(selected) < 1 or max(selected) > len(document):
                raise DomainError("INVALID_PAGE_SCOPE", "物理页范围超出PDF")
            if config.get("physical_pages"):
                warnings.append("selected_pages_not_full_document")
            for number in sorted(set(selected)):
                if checkpoint: checkpoint()
                text = document[number - 1].get_text("text", sort=False).replace("\r\n", "\n")
                if not text.strip():
                    warnings.append(f"page_{number}_no_text_requires_ocr")
                frames.append({"physical_page": number, "text": text, "text_origin": "pymupdf_native"})
            # Identity check uses the actual opening pages even for selected-page parsing.
            opening = "\n".join(document[n].get_text("text") for n in range(min(10, len(document))))
            identity = {"name": "PyMuPDF", "version": fitz.VersionBind, "page_count": len(document),
                        "opening": opening}
    elif file_format == "txt":
        text = data.decode("utf-8-sig")
        frames = [{"physical_page": None, "text": text, "text_origin": "utf8_text"}]
        identity = {"name": "utf8", "version": "1", "opening": text[:30000]}
    elif file_format == "docx":
        with ZipFile(BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 100 * 1024 * 1024:
                raise DomainError("DOCX_TOO_LARGE", "DOCX正文解压大小超限")
            xml = ET.fromstring(archive.read(info))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        text = "\n".join("".join(t.text or "" for t in n.findall(".//w:t", ns))
                         for n in xml.findall(".//w:p", ns))
        frames = [{"physical_page": None, "text": text, "text_origin": "docx_body_xml"}]
        if xml.find(".//w:numPr", ns) is not None:
            warnings.append("docx_automatic_numbering_not_expanded")
        identity = {"name": "docx_xml_body", "version": "1", "opening": text[:30000]}
    else:
        raise DomainError("UNSUPPORTED_FORMAT", "不支持的文档格式")
    if not any(f["text"].strip() for f in frames):
        raise DomainError("NO_SEARCHABLE_TEXT", "没有可检索文字，请先准备可核查的OCR文字")
    for frame in frames:
        if checkpoint: checkpoint()
        frame["text_sha256"] = sha(frame["text"].encode())
        frame["spans"] = []
        for start in range(0, len(frame["text"]), size - overlap):
            end = min(start + size, len(frame["text"]))
            piece = frame["text"][start:end]
            if piece.strip():
                frame["spans"].append((start, end, piece))
    return frames, identity, warnings

def check_annual_identity(opening, stock_code, stock_name, year):
    normalized = re.sub(r"\s+", "", opening)
    if "年度报告摘要" in normalized[:2500] or "半年度报告" in normalized[:2500]:
        raise DomainError("NOT_FULL_ANNUAL_REPORT", "不能把摘要或半年报作为完整年报")
    year_match = str(year) + "年" in normalized
    issuer_match = stock_code in normalized or stock_name in normalized
    if not year_match or not issuer_match:
        raise DomainError("ANNUAL_IDENTITY_MISMATCH", "开篇未同时找到指定公司与报告年度，需人工核查原件")
    return "opening_identity_matched"
