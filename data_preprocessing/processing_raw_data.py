import docx
import hashlib
import json
import os
import re
import unicodedata
import pdfplumber
import subprocess

from docx.document import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import _Cell, Table
from docx.text.paragraph import Paragraph

try:
    from transformers import AutoTokenizer
except ImportError:
    AutoTokenizer = None

# ============================================================
# CONFIG
# ============================================================
DATA_DIR = "data_raw"
OUTPUT_JSON = "uit_dataset.json"
TOKENIZER_MODEL = "bkai-foundation-models/vietnamese-bi-encoder"
MAX_CHILD_TOKENS = 350
CHILD_TOKEN_OVERLAP = 50

SUPPORTED_EXTENSIONS = {".docx", ".pdf"}

CHAPTER_PATTERN = re.compile(r"^\s*(?:CHƯƠNG|Chương)\s+([IVXLCDM]+|\d+)\b(?:[\s.:\-–—]+(.*))?$", re.I)
ARTICLE_PATTERN = re.compile(r"^\s*Điều\s+(\d+[A-Za-z]?)\s*[\.:\-–—]?\s*(.*)$", re.I)
CLAUSE_PATTERN = re.compile(r"^\s*(\d{1,3})\.\s+(\S.*)$", re.S)
POINT_PATTERN = re.compile(r"^\s*([a-zđ])\s*[\)\.]\s+(\S.*)$", re.I | re.S)

_TOKENIZER = None

# ============================================================
# PHẦN 1: UTILITIES CHUNG
# ============================================================
def iter_block_items(parent):
    """Duyệt Paragraph và Table theo đúng thứ tự xuất hiện trong DOCX."""
    if isinstance(parent, Document):
        parent_elm = parent.element.body
    elif isinstance(parent, _Cell):
        parent_elm = parent._tc
    else:
        raise TypeError("Chỉ hỗ trợ Document hoặc Cell")

    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)

def normalize_unicode(text):
    if text is None:
        return ""
    text = unicodedata.normalize("NFC", str(text))
    text = text.replace("\u00a0", " ")
    text = text.replace("\u200b", "")
    text = text.replace("\ufeff", "")
    return text

def normalize_whitespace(text):
    """nhiều space/tab → 1 space
        strip đầu cuối từng dòng
        3+ dòng trắng → tối đa 1 dòng trắng"""
    text = normalize_unicode(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def stable_id(*parts):
    raw = "|".join(str(p or "") for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

def slugify_identifier(value):
    """Chuẩn hóa số hiệu văn bản hoặc filename thành ID an toàn. Ex: 123/QĐ-ĐHCNTT =>  123_QĐ-ĐHCNTT"""
    value = normalize_unicode(value or "")
    value = value.upper().strip()
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[^0-9A-ZÀ-ỸĐ._\-/]", "", value)
    value = value.replace("/", "_").replace("\\", "_")
    return value or "DOC"

def roman_to_int(value):
    value = str(value or "").upper().strip()
    if value.isdigit():
        return int(value)
    roman_map = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    previous = 0
    for char in reversed(value):
        current = roman_map.get(char, 0)
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    return total if total > 0 else None

def classify_line(text):
    text = normalize_whitespace(text)
    if not text:
        return "text"
    if CHAPTER_PATTERN.match(text):
        return "chapter"
    if ARTICLE_PATTERN.match(text):
        return "article"
    return "text"

def parse_chapter_heading(text):
    match = CHAPTER_PATTERN.match(normalize_whitespace(text))
    if not match:
        return None, normalize_whitespace(text)
    number_raw = match.group(1)
    title = normalize_whitespace(match.group(2) or "")
    return roman_to_int(number_raw), title

def parse_article_heading(text):
    match = ARTICLE_PATTERN.match(normalize_whitespace(text))
    if not match:
        return None, normalize_whitespace(text)
    return match.group(1), normalize_whitespace(match.group(2) or "")

def get_tokenizer():
    global _TOKENIZER
    if _TOKENIZER is not None:
        return _TOKENIZER
    if AutoTokenizer is None:
        return None
    try:
        _TOKENIZER = AutoTokenizer.from_pretrained(TOKENIZER_MODEL, use_fast=False)
    except Exception as exc:
        print(f"⚠️ Không tải được tokenizer {TOKENIZER_MODEL}: {exc}. Sẽ dùng word-based fallback.")
        _TOKENIZER = None
    return _TOKENIZER

def count_tokens(text):
    tokenizer = get_tokenizer()
    if tokenizer is None:
        return len(text.split())
    return len(tokenizer.encode(text, add_special_tokens=False))

def split_text_by_tokens(text, max_tokens=MAX_CHILD_TOKENS, overlap=CHILD_TOKEN_OVERLAP):
    text = normalize_whitespace(text)
    if not text:
        return []

    tokenizer = get_tokenizer()
    if tokenizer is None:
        words = text.split()
        if len(words) <= max_tokens:
            return [text]
        step = max(1, max_tokens - overlap)
        return [" ".join(words[i:i + max_tokens]).strip() for i in range(0, len(words), step) if words[i:i + max_tokens]]

    token_ids = tokenizer.encode(text, add_special_tokens=False)
    if len(token_ids) <= max_tokens:
        return [text]

    step = max(1, max_tokens - overlap)
    chunks = []
    for start in range(0, len(token_ids), step):
        piece = token_ids[start:start + max_tokens]
        if not piece:
            break
        decoded = normalize_whitespace(tokenizer.decode(piece, skip_special_tokens=True))
        if decoded:
            chunks.append(decoded)
        if start + max_tokens >= len(token_ids):
            break
    return chunks

# ============================================================
# PHẦN 2: TABLE HANDLING
# ============================================================
def normalize_table(table_data):
    if not table_data:
        return []
    normalized = []
    for row in table_data:
        clean_row = []
        for cell in row or []:
            value = normalize_whitespace(cell or "")
            clean_row.append(value)
        while clean_row and not clean_row[-1]:
            clean_row.pop()
        if any(clean_row):
            normalized.append(clean_row)
    return normalized

def table_to_markdown(table_data):
    table_data = normalize_table(table_data)
    if not table_data:
        return ""

    width = max(len(row) for row in table_data)
    rows = [row + [""] * (width - len(row)) for row in table_data]
    escaped = [[cell.replace("|", "\\|").replace("\n", " ") for cell in row] for row in rows]
    header = escaped[0]
    separator = ["---"] * width
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(separator) + " |"]
    for row in escaped[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)

def table_to_search_text(table_data):
    table_data = normalize_table(table_data)
    if not table_data:
        return ""

    headers = table_data[0]
    body = table_data[1:]
    valid_headers = sum(bool(h) for h in headers) >= max(1, len(headers) // 2)
    sentences = []

    if valid_headers and body:
        for row in body:
            pairs = []
            for index, value in enumerate(row):
                if not value:
                    continue
                header = headers[index] if index < len(headers) else ""
                pairs.append(f"{header}: {value}" if header else value)
            if pairs:
                sentences.append("; ".join(pairs) + ".")
    else:
        for row in table_data:
            values = [value for value in row if value]
            if values:
                sentences.append("; ".join(values) + ".")

    return " ".join(sentences)

def is_administrative_table(table_data):
    text = " ".join(" ".join(row) for row in normalize_table(table_data)).upper()
    noise_keywords = ["ĐẠI HỌC QUỐC GIA", "CỘNG HÒA XÃ HỘI", "ĐỘC LẬP", "TỰ DO", "HẠNH PHÚC"]
    return sum(keyword in text for keyword in noise_keywords) >= 2

# ============================================================
# PHẦN 3: EXTRACT DOCX / PDF THÀNH BLOCK CHUNG
# Block chuẩn: {type, content/table, page}
# ============================================================
import os
import win32com.client


def convert_doc_to_docx(folder_path):
    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False

    try:
        for filename in os.listdir(folder_path):
            if not filename.lower().endswith(".doc") or filename.lower().endswith(".docx"):
                continue

            doc_path = os.path.abspath(os.path.join(folder_path, filename))
            docx_path = os.path.splitext(doc_path)[0] + ".docx"

            if os.path.exists(docx_path):
                print(f"⏭️ Đã tồn tại: {docx_path}")
                continue

            print(f"🔄 {filename}")

            document = word.Documents.Open(doc_path)
            document.SaveAs2(docx_path, FileFormat=16)
            document.Close()

            print(f"✅ {filename} → {os.path.basename(docx_path)}")

    finally:
        word.Quit()

def extract_docx(file_path):
    try:
        document = docx.Document(file_path)
    except Exception as exc:
        print(f"❌ Không đọc được DOCX {file_path}: {exc}")
        return []

    blocks = []
    chapter_counter = 0
    article_counter = 0

    for block in iter_block_items(document):
        if isinstance(block, Paragraph):
            text = normalize_whitespace(block.text)
            if not text:
                continue

            style = (block.style.name or "").lower()
            detected_type = classify_line(text)

            if detected_type == "chapter":
                chapter_number, _ = parse_chapter_heading(text)
                if chapter_number is not None:
                    chapter_counter = chapter_number
                blocks.append({"type": "chapter", "content": text, "page": None})
                continue

            if detected_type == "article":
                article_number, _ = parse_article_heading(text)
                if article_number and str(article_number).isdigit():
                    article_counter = int(article_number)
                blocks.append({"type": "article", "content": text, "page": None})
                continue

            if "heading 1" in style:
                chapter_counter += 1
                blocks.append({"type": "chapter", "content": f"Chương {chapter_counter}. {text}", "page": None})
                continue

            if "heading 2" in style:
                article_counter += 1
                blocks.append({"type": "article", "content": f"Điều {article_counter}. {text}", "page": None})
                continue

            blocks.append({"type": "text", "content": text, "page": None})

        elif isinstance(block, Table):
            table_data = [[normalize_whitespace(cell.text) for cell in row.cells] for row in block.rows]
            table_data = normalize_table(table_data)
            if table_data and not is_administrative_table(table_data):
                blocks.append({"type": "table", "table": table_data, "page": None})

    return merge_split_headings(blocks)


def point_inside_bbox(x, y, bbox):
    x0, top, x1, bottom = bbox
    return x0 <= x <= x1 and top <= y <= bottom


def extract_pdf_page_items(page, page_number):
    page_items = []

    try:
        found_tables = page.find_tables()
    except Exception:
        found_tables = []

    table_bboxes = [table.bbox for table in found_tables]
    valid_tables = []

    for table in found_tables:
        try:
            table_data = normalize_table(table.extract())
        except Exception:
            table_data = []

        if table_data and not is_administrative_table(table_data):
            valid_tables.append({
                "bbox": table.bbox,
                "data": table_data
            })

    table_bboxes = [item["bbox"] for item in valid_tables]

    try:
        words = page.extract_words(use_text_flow=True, keep_blank_chars=False, x_tolerance=2, y_tolerance=3) or []
    except Exception:
        words = []

    filtered_words = []
    for word in words:
        center_x = (float(word["x0"]) + float(word["x1"])) / 2
        center_y = (float(word["top"]) + float(word["bottom"])) / 2
        if any(point_inside_bbox(center_x, center_y, bbox) for bbox in table_bboxes):
            continue
        filtered_words.append(word)

    line_groups = []
    for word in sorted(filtered_words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        top = float(word["top"])
        target = None
        for group in line_groups:
            if abs(group["top"] - top) <= 3:
                target = group
                break
        if target is None:
            target = {"top": top, "words": []}
            line_groups.append(target)
        target["words"].append(word)

    for group in line_groups:
        line = " ".join(word["text"] for word in sorted(group["words"], key=lambda item: float(item["x0"])))
        line = normalize_whitespace(line)
        if line:
            page_items.append({"sort_y": group["top"], "block": {"type": classify_line(line), "content": line, "page": page_number}})

    for item in valid_tables:
        page_items.append({
            "sort_y": float(item["bbox"][1]),
            "block": {
                "type": "table",
                "table": item["data"],
                "page": page_number
            }
        })

    if not page_items:
        fallback_text = page.extract_text() or ""
        for index, line in enumerate(fallback_text.splitlines()):
            line = normalize_whitespace(line)
            if line:
                page_items.append({"sort_y": float(index), "block": {"type": classify_line(line), "content": line, "page": page_number}})

    page_items.sort(key=lambda item: item["sort_y"])
    return [item["block"] for item in page_items]


def extract_pdf(file_path):
    blocks = []
    try:
        with pdfplumber.open(file_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                blocks.extend(extract_pdf_page_items(page, page_number))
    except Exception as exc:
        print(f"❌ Không đọc được PDF {file_path}: {exc}")
        return []
    return merge_split_headings(blocks)


def uppercase_ratio(text):
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    return sum(char.isupper() for char in letters) / len(letters)


def merge_split_headings(blocks):
    """Ghép trường hợp PDF/DOCX tách 'Chương I' và tên chương ở dòng kế tiếp."""
    merged = []
    index = 0
    while index < len(blocks):
        current = blocks[index]
        if current.get("type") == "chapter" and index + 1 < len(blocks):
            next_block = blocks[index + 1]
            _, chapter_title = parse_chapter_heading(current.get("content", ""))
            next_text = next_block.get("content", "") if next_block.get("type") == "text" else ""
            if not chapter_title and next_text and len(next_text) <= 180 and uppercase_ratio(next_text) >= 0.6 and not CLAUSE_PATTERN.match(next_text):
                current = dict(current)
                current["content"] = f"{current['content']} - {next_text}"
                merged.append(current)
                index += 2
                continue
        merged.append(current)
        index += 1
    return merged

# ============================================================
# PHẦN 4: METADATA — LUÔN EXTRACT TRƯỚC CLEAN
# ============================================================
def build_raw_text_for_metadata(blocks, max_blocks=100):
    lines = []
    for block in blocks[:max_blocks]:
        if block.get("type") in {"text", "chapter", "article"}:
            content = normalize_whitespace(block.get("content", ""))
            if content:
                lines.append(content)
        elif block.get("type") == "table":
            table_text = table_to_search_text(block.get("table", []))
            if table_text:
                lines.append(table_text)
    return "\n".join(lines)


def extract_metadata(text, filename):
    text = normalize_unicode(text)
    metadata = {
        "source_file": filename,
        "document_type": None,
        "document_title": None,
        "doc_number": None,
        "issued_date": None,
        "issued_date_raw": None,
        "effective_date": None,
        "effective_date_raw": None,
        "effective_rule": None,
    }

    type_match = re.search(r"\b(QUY ĐỊNH|QUY TRÌNH|QUY CHẾ|HƯỚNG DẪN|QUYẾT ĐỊNH|THÔNG BÁO)\b", text, re.I)
    if type_match:
        metadata["document_type"] = normalize_whitespace(type_match.group(1)).upper()

    doc_number_match = re.search(r"(?im)^\s*Số\s*:\s*([0-9A-Za-zÀ-ỹĐđ./\-]+)", text)
    if doc_number_match:
        metadata["doc_number"] = re.sub(r"\s+", "", doc_number_match.group(1)).strip(".;,")

    issued_date_match = re.search(r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})", text, re.I)
    if issued_date_match:
        day, month, year = issued_date_match.groups()
        metadata["issued_date"] = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        metadata["issued_date_raw"] = issued_date_match.group(0)

    explicit_effective_match = re.search(r"(?:có\s+hiệu\s+lực|hiệu\s+lực\s+thi\s+hành).*?(?:từ|kể\s+từ)\s+ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})", text, re.I | re.S)
    if explicit_effective_match:
        day, month, year = explicit_effective_match.groups()
        metadata["effective_date"] = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        metadata["effective_date_raw"] = explicit_effective_match.group(0)
    else:
        effective_from_signing = re.search(r"(?:có\s+hiệu\s+lực|hiệu\s+lực\s+thi\s+hành).*?kể\s+từ\s+ngày\s+ký", text, re.I | re.S)
        if effective_from_signing:
            metadata["effective_rule"] = "kể từ ngày ký"
            metadata["effective_date"] = metadata["issued_date"]

    lines = [normalize_whitespace(line) for line in text.splitlines() if normalize_whitespace(line)]
    administrative_patterns = [r"^ĐẠI HỌC QUỐC GIA", r"^TRƯỜNG ĐẠI HỌC", r"^CỘNG HÒA", r"^Độc lập", r"^Số\s*:", r"^Thành phố Hồ Chí Minh"]
    title_start = None
    for index, line in enumerate(lines):
        if re.search(r"\b(QUY ĐỊNH|QUY TRÌNH|QUY CHẾ|HƯỚNG DẪN|QUYẾT ĐỊNH|THÔNG BÁO)\b", line, re.I):
            title_start = index
            break

    if title_start is not None:
        title_parts = []
        for line in lines[title_start:title_start + 4]:
            if any(re.search(pattern, line, re.I) for pattern in administrative_patterns):
                if title_parts:
                    break
                continue
            if CHAPTER_PATTERN.match(line) or ARTICLE_PATTERN.match(line):
                break
            title_parts.append(line)
        if title_parts:
            metadata["document_title"] = " ".join(title_parts)

    if not metadata["document_title"]:
        metadata["document_title"] = os.path.splitext(filename)[0]

    base_identifier = metadata["doc_number"] or os.path.splitext(filename)[0]
    metadata["doc_id"] = slugify_identifier(base_identifier)
    return metadata

# ============================================================
# PHẦN 5: CLEAN — CHỈ XÓA NOISE CHẮC CHẮN
# ============================================================
def clean_administrative(text):
    if not text:
        return ""

    text = normalize_unicode(text)

    full_line_noise_patterns = [
        r"^\s*TRƯỜNG\s+ĐẠI\s+HỌC\s+CÔNG\s+NGHỆ\s+THÔNG\s+TIN\s*$",
        r"^\s*ĐẠI\s+HỌC\s+QUỐC\s+GIA\s+(?:THÀNH\s+PHỐ\s+HỒ\s+CHÍ\s+MINH|TP\.?\s*HCM)\s*$",
        r"^\s*CỘNG\s+HÒA\s+XÃ\s+HỘI\s+CHỦ\s+NGHĨA\s+VIỆT\s+NAM\s*$",
        r"^\s*Độc\s+lập\s*[-–—]\s*Tự\s+do\s*[-–—]\s*Hạnh\s+phúc\s*$",
        r"^\s*Thành\s+phố\s+Hồ\s+Chí\s+Minh\s*,?\s*ngày\s+\d{1,2}\s+tháng\s+\d{1,2}\s+năm\s+\d{4}\s*$",
        r"^\s*Số\s*:\s*[0-9A-Za-zÀ-ỹĐđ./\-]+\s*$",
        r"^\s*Nơi\s+nhận\s*:.*$",
        r"^\s*-\s*Lưu\s*:.*$",
        r"^\s*-\s*Như\s+Điều\s+\d+.*$",
        r"^\s*\(Đã\s+ký(?:.*)?\)\s*$",
        r"^\s*KT\.\s*HIỆU\s+TRƯỞNG\s*$",
        r"^\s*PHÓ\s+HIỆU\s+TRƯỞNG\s*$",
        r"^\s*HIỆU\s+TRƯỞNG\s*$",
    ]

    cleaned_lines = []
    for line in text.splitlines():
        line = normalize_whitespace(line)
        if not line:
            continue
        if any(re.match(pattern, line, re.I) for pattern in full_line_noise_patterns):
            continue
        line = re.sub(r"^inh\s+viên\b", "Sinh viên", line, flags=re.I)
        line = re.sub(r"^iều\s+(\d+)", r"Điều \1", line, flags=re.I)
        line = re.sub(r"^hương\s+([IVXLCDM\d]+)", r"Chương \1", line, flags=re.I)
        cleaned_lines.append(line)

    return normalize_whitespace("\n".join(cleaned_lines))

def clean_blocks(blocks):
    cleaned = []
    for block in blocks:
        block_type = block.get("type")
        if block_type == "table":
            table_data = normalize_table(block.get("table", []))
            if table_data and not is_administrative_table(table_data):
                cleaned.append({**block, "table": table_data})
            continue

        content = clean_administrative(block.get("content", ""))
        if not content:
            continue
        detected_type = classify_line(content)
        if block_type in {"chapter", "article"}:
            detected_type = block_type
        cleaned.append({**block, "type": detected_type, "content": content})
    return cleaned

# ============================================================
# PHẦN 6: HIERARCHICAL CHUNKING
# Document -> Chapter -> Article(parent) -> Clause/Point(child)
# ============================================================
def make_context_prefix(document_metadata, chapter_number=None, chapter_title=None, article_number=None, article_title=None, clause_number=None, point_label=None):
    parts = []
    title = document_metadata.get("document_title")
    doc_number = document_metadata.get("doc_number")
    if title:
        parts.append(title)
    if doc_number:
        parts.append(f"Số hiệu {doc_number}")
    if chapter_number is not None or chapter_title:
        chapter_text = f"Chương {chapter_number}" if chapter_number is not None else "Chương"
        if chapter_title:
            chapter_text += f". {chapter_title}"
        parts.append(chapter_text)
    if article_number is not None or article_title:
        article_text = f"Điều {article_number}" if article_number is not None else "Điều"
        if article_title:
            article_text += f". {article_title}"
        parts.append(article_text)
    if clause_number is not None:
        parts.append(f"Khoản {clause_number}")
    if point_label is not None:
        parts.append(f"Điểm {point_label}")
    return ". ".join(part.strip(" .") for part in parts if part).strip() + "."

def make_retrieval_text(document_metadata, content, table_search_text="", chapter_number=None, chapter_title=None, article_number=None, article_title=None, clause_number=None, point_label=None):
    prefix = make_context_prefix(document_metadata, chapter_number, chapter_title, article_number, article_title, clause_number, point_label)
    body = normalize_whitespace(content)
    table_text = normalize_whitespace(table_search_text)
    return normalize_whitespace("\n".join(part for part in [prefix, body, table_text] if part))

def create_child_chunks(document_metadata, parent, article_blocks):
    units = []
    current_clause = None
    current_point = None
    preamble_parts = []

    def flush_point():
        nonlocal current_point
        if current_point and (current_point["parts"] or current_point["tables"]):
            units.append({"unit_type": "point", **current_point})
        current_point = None

    def flush_clause():
        nonlocal current_clause
        flush_point()
        if current_clause and (current_clause["parts"] or current_clause["tables"]):
            units.append({"unit_type": "clause", **current_clause})
        current_clause = None

    def flush_preamble():
        nonlocal preamble_parts
        if preamble_parts:
            units.append({"unit_type": "preamble", "clause_number": None, "point_label": None, "parts": preamble_parts[:], "tables": [], "page": parent.get("page_start")})
        preamble_parts = []

    for block in article_blocks:
        if block.get("type") == "table":
            table_entry = {"raw": block.get("table", []), "markdown": table_to_markdown(block.get("table", [])), "search_text": table_to_search_text(block.get("table", [])), "page": block.get("page")}
            if current_point is not None:
                current_point["tables"].append(table_entry)
            elif current_clause is not None:
                current_clause["tables"].append(table_entry)
            else:
                flush_preamble()
                units.append({"unit_type": "table", "clause_number": None, "point_label": None, "parts": [], "tables": [table_entry], "page": block.get("page")})
            continue

        text = normalize_whitespace(block.get("content", ""))
        if not text:
            continue

        clause_match = CLAUSE_PATTERN.match(text)
        point_match = POINT_PATTERN.match(text)

        if clause_match:
            flush_preamble()
            flush_clause()
            current_clause = {"clause_number": clause_match.group(1), "point_label": None, "parts": [f"{clause_match.group(1)}. {clause_match.group(2)}"], "tables": [], "page": block.get("page")}
            continue

        if point_match and current_clause is not None:
            flush_point()
            current_point = {"clause_number": current_clause.get("clause_number"), "point_label": point_match.group(1).lower(), "parts": [f"{point_match.group(1).lower()}) {point_match.group(2)}"], "tables": [], "page": block.get("page")}
            continue

        if current_point is not None:
            current_point["parts"].append(text)
        elif current_clause is not None:
            current_clause["parts"].append(text)
        else:
            preamble_parts.append(text)

    flush_preamble()
    flush_clause()

    children = []
    sequence = 0
    for unit in units:
        content = normalize_whitespace("\n".join(unit.get("parts", [])))
        table_markdowns = [table["markdown"] for table in unit.get("tables", []) if table.get("markdown")]
        table_search_texts = [table["search_text"] for table in unit.get("tables", []) if table.get("search_text")]
        display_content = normalize_whitespace("\n\n".join(part for part in [content, *table_markdowns] if part))
        searchable_table_text = normalize_whitespace(" ".join(table_search_texts))

        if not display_content and not searchable_table_text:
            continue

        fallback_text = display_content or searchable_table_text
        split_parts = split_text_by_tokens(fallback_text, MAX_CHILD_TOKENS, CHILD_TOKEN_OVERLAP)
        if not split_parts:
            split_parts = [fallback_text]

        for split_index, split_content in enumerate(split_parts, start=1):
            sequence += 1
            clause_number = unit.get("clause_number")
            point_label = unit.get("point_label")
            suffix_parts = [parent["parent_id"], unit.get("unit_type"), clause_number, point_label, split_index]
            chunk_id = f"{document_metadata['doc_id']}_C_{stable_id(*suffix_parts)}"
            retrieval_text = make_retrieval_text(document_metadata, split_content, searchable_table_text if split_index == 1 else "", parent.get("chapter_number"), parent.get("chapter_title"), parent.get("article_number"), parent.get("article_title"), clause_number, point_label)

            children.append({
                "chunk_id": chunk_id,
                "parent_id": parent["parent_id"],
                "chunk_type": unit.get("unit_type"),
                "sequence": sequence,
                "content": split_content,
                "retrieval_text": retrieval_text,
                "tables": unit.get("tables", []) if split_index == 1 else [],
                "metadata": {
                    "doc_id": document_metadata["doc_id"],
                    "source_file": document_metadata["source_file"],
                    "document_title": document_metadata.get("document_title"),
                    "doc_number": document_metadata.get("doc_number"),
                    "issued_date": document_metadata.get("issued_date"),
                    "effective_date": document_metadata.get("effective_date"),
                    "chapter_number": parent.get("chapter_number"),
                    "chapter_title": parent.get("chapter_title"),
                    "article_number": parent.get("article_number"),
                    "article_title": parent.get("article_title"),
                    "clause_number": clause_number,
                    "point_label": point_label,
                    "page": unit.get("page"),
                    "split_index": split_index,
                    "split_count": len(split_parts),
                },
            })

    return children

def build_parent_chunk(document_metadata, chapter_number, chapter_title, article_number, article_title, article_blocks, parent_sequence):
    content_parts = []
    pages = []
    tables = []

    for block in article_blocks:
        page = block.get("page")
        if page is not None:
            pages.append(page)
        if block.get("type") == "table":
            table_data = block.get("table", [])
            table_markdown = table_to_markdown(table_data)
            table_search_text = table_to_search_text(table_data)
            tables.append({"raw": table_data, "markdown": table_markdown, "search_text": table_search_text, "page": page})
            if table_markdown:
                content_parts.append(table_markdown)
        else:
            content = normalize_whitespace(block.get("content", ""))
            if content:
                content_parts.append(content)

    parent_content = normalize_whitespace("\n\n".join(content_parts))
    parent_seed = [document_metadata["doc_id"], chapter_number, chapter_title, article_number, article_title, parent_sequence]
    parent_id = f"{document_metadata['doc_id']}_P_{stable_id(*parent_seed)}"
    retrieval_text = make_retrieval_text(document_metadata, parent_content, " ".join(table["search_text"] for table in tables if table.get("search_text")), chapter_number, chapter_title, article_number, article_title)

    return {
        "parent_id": parent_id,
        "parent_type": "article" if article_number is not None else "general",
        "sequence": parent_sequence,
        "content": parent_content,
        "retrieval_text": retrieval_text,
        "chapter_number": chapter_number,
        "chapter_title": chapter_title,
        "article_number": article_number,
        "article_title": article_title,
        "page_start": min(pages) if pages else None,
        "page_end": max(pages) if pages else None,
        "tables": tables,
    }

def process_blocks_to_hierarchy(blocks, document_metadata):
    parents = []
    children = []

    current_chapter_number = None
    current_chapter_title = None
    current_article_number = None
    current_article_title = None
    current_article_blocks = []
    parent_sequence = 0

    def flush_article():
        nonlocal current_article_blocks, parent_sequence
        if not current_article_blocks:
            return
        parent_sequence += 1
        parent = build_parent_chunk(document_metadata, current_chapter_number, current_chapter_title, current_article_number, current_article_title, current_article_blocks, parent_sequence)
        if parent["content"]:
            parents.append(parent)
            children.extend(create_child_chunks(document_metadata, parent, current_article_blocks))
        current_article_blocks = []

    for block in blocks:
        block_type = block.get("type")

        if block_type == "chapter":
            flush_article()
            current_chapter_number, current_chapter_title = parse_chapter_heading(block.get("content", ""))
            current_article_number = None
            current_article_title = None
            continue

        if block_type == "article":
            flush_article()
            current_article_number, current_article_title = parse_article_heading(block.get("content", ""))
            continue

        current_article_blocks.append(block)

    flush_article()
    return parents, children

# ============================================================
# PHẦN 7: PIPELINE CHÍNH
# ============================================================
# DEBUG_PHRASE = "có quan hệ ruột thịt"
# def debug_find_phrase(blocks, phrase, label):
#     phrase = phrase.lower()

#     for i, block in enumerate(blocks):
#         if block.get("type") == "table":
#             text = table_to_search_text(block.get("table", []))
#         else:
#             text = block.get("content", "")

#         if phrase in text.lower():
#             print(f"✅ [{label}] FOUND block={i}")
#             print(text)
#             return True

#     print(f"❌ [{label}] NOT FOUND")
#     return False

def process_single_file(file_path):
    filename = os.path.basename(file_path)
    extension = os.path.splitext(filename)[1].lower()

    if extension == ".docx":
        raw_blocks = extract_docx(file_path)
    elif extension == ".pdf":
        raw_blocks = extract_pdf(file_path)
    else:
        return None

    if not raw_blocks:
        print(f"⚠️ Không có dữ liệu hợp lệ trong {filename}")
        return None

    raw_text_for_metadata = build_raw_text_for_metadata(raw_blocks)
    document_metadata = extract_metadata(raw_text_for_metadata, filename)
    document_metadata["source_type"] = extension.lstrip(".")

    cleaned_blocks = clean_blocks(raw_blocks)
    parents, chunks = process_blocks_to_hierarchy(cleaned_blocks, document_metadata)
    
    return {
        "doc_id": document_metadata["doc_id"],
        "source_file": filename,
        "metadata": document_metadata,
        "parents": parents,
        "chunks": chunks,
        "stats": {
            "raw_blocks": len(raw_blocks),
            "clean_blocks": len(cleaned_blocks),
            "parents": len(parents),
            "chunks": len(chunks),
        },
    }

def process_pipeline(folder_path):
    result = {"schema_version": "3.0", "documents": []}

    if not os.path.isdir(folder_path):
        raise FileNotFoundError(f"Không tìm thấy thư mục dữ liệu: {folder_path}")

    filenames = sorted(os.listdir(folder_path))
    supported_files = [name for name in filenames if os.path.splitext(name)[1].lower() in SUPPORTED_EXTENSIONS and not name.startswith("~$")]

    print(f"📚 Tìm thấy {len(supported_files)} file cần xử lý.")

    for index, filename in enumerate(supported_files, start=1):
        file_path = os.path.join(folder_path, filename)
        print(f"[{index}/{len(supported_files)}] Processing: {filename}")
        try:
            document = process_single_file(file_path)
            if document is not None:
                result["documents"].append(document)
                print(f"   ✅ parents={document['stats']['parents']} | chunks={document['stats']['chunks']}")
        except Exception as exc:
            print(f"   ❌ Lỗi {filename}: {exc}")

    return result

def save_json(data, output_path):
    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    print(f"✅ Đã lưu dataset: {output_path}")

if __name__ == "__main__":
    convert_doc_to_docx(DATA_DIR)
    final_data = process_pipeline(DATA_DIR)
    save_json(final_data, OUTPUT_JSON)