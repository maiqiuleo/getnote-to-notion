#!/usr/bin/env python3
"""
Get 笔记 → Notion get 笔记数据库 自动同步

功能：
- 按最近更新时间检查需要同步的笔记
- 首次创建，后续更新（支持 AI 笔记/追加笔记变更后重新同步）
- 保留标签、知识库分类、创建时间等元数据
"""

import json
import os
import re
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

# ============ 配置 ============
GETNOTE_API_KEY = os.environ.get("GETNOTE_API_KEY", "")
GETNOTE_CLIENT_ID = os.environ.get("GETNOTE_CLIENT_ID", "")

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_VERSION = "2022-06-28"
NOTION_DB_ID = os.environ.get("NOTION_DB_ID", "")

CHECK_HOURS = int(os.environ.get("CHECK_HOURS", "6"))

TZ_CN = timezone(timedelta(hours=8))  # Get 笔记 API 返回时间均为北京时间（UTC+8）


# ============ 辅助函数 ============

def getnote_request(path, method="GET", body=None):
    """发送 Get 笔记 API 请求（含指数退避重试）。"""
    url = f"https://openapi.biji.com{path}"
    data = json.dumps(body).encode() if body is not None else None

    for attempt in range(3):
        req = Request(url, data=data, method=method)
        req.add_header("Authorization", GETNOTE_API_KEY)
        req.add_header("X-Client-ID", GETNOTE_CLIENT_ID)
        if body is not None:
            req.add_header("Content-Type", "application/json")

        ctx = ssl.create_default_context()
        try:
            with urlopen(req, context=ctx, timeout=30) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            if e.code == 429:
                wait = 30
                print(f"[WARN] Get笔记 API 限流 (429)，等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            error_body = e.read().decode()[:800]
            print(f"[ERROR] Get笔记 API error {e.code}: {error_body}")
            if attempt < 2:
                wait = 2 ** attempt
                print(f"[WARN] 第 {attempt + 1} 次重试，等待 {wait}s...")
                time.sleep(wait)
                continue
            return None
        except Exception as e:
            print(f"[ERROR] Get笔记 request failed: {e}")
            if attempt < 2:
                wait = 2 ** attempt
                print(f"[WARN] 第 {attempt + 1} 次重试，等待 {wait}s...")
                time.sleep(wait)
                continue
            return None

    return None


def notion_request(path, body=None, method="POST"):
    """发送 Notion API 请求（含指数退避重试）。"""
    url = f"https://api.notion.com/v1{path}"
    data = json.dumps(body).encode() if body is not None else None

    for attempt in range(3):
        req = Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {NOTION_TOKEN}")
        req.add_header("Notion-Version", NOTION_VERSION)
        req.add_header("Content-Type", "application/json")

        ctx = ssl.create_default_context()
        try:
            with urlopen(req, context=ctx, timeout=30) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            if e.code == 429:
                wait = 30
                print(f"[WARN] Notion API 限流 (429)，等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            error_body = e.read().decode()[:1200]
            print(f"[ERROR] Notion API error {e.code}: {error_body}")
            if attempt < 2:
                wait = 2 ** attempt
                print(f"[WARN] 第 {attempt + 1} 次重试，等待 {wait}s...")
                time.sleep(wait)
                continue
            return None
        except Exception as e:
            print(f"[ERROR] Notion request failed: {e}")
            if attempt < 2:
                wait = 2 ** attempt
                print(f"[WARN] 第 {attempt + 1} 次重试，等待 {wait}s...")
                time.sleep(wait)
                continue
            return None

    return None


def fetch_getnote_notes():
    """从 Get 笔记拉取笔记列表（过滤子笔记/追加笔记本身）。"""
    all_notes = []
    since_id = 0
    max_iterations = 20
    iteration = 0

    while iteration < max_iterations:
        print(f"[INFO] 正在获取 Get 笔记 (since_id={since_id})...")
        result = getnote_request(f"/open/api/v1/resource/note/list?since_id={since_id}")

        if not result or not result.get("success"):
            error_msg = result.get("message", "未知错误") if result else "请求失败"
            print(f"[ERROR] 获取笔记列表失败: {error_msg}")
            break

        data = result.get("data") or {}
        notes = data.get("notes", [])
        if not notes:
            break

        # 过滤追加笔记本身（is_child_note=True），只保留独立笔记
        parent_notes = [n for n in notes if not n.get("is_child_note")]
        all_notes.extend(parent_notes)

        # 优先使用 API 返回的 cursor，回退到最后一条笔记 ID
        next_since_id = data.get("cursor") or notes[-1].get("id", 0)
        if str(next_since_id) == str(since_id):
            break

        since_id = next_since_id
        iteration += 1

        has_more = data.get("has_more", len(notes) >= 20)
        if not has_more:
            break

    print(f"[INFO] 共获取 {len(all_notes)} 条笔记")
    return all_notes


def fetch_note_detail(note_id):
    """获取单条笔记详情。"""
    result = getnote_request(f"/open/api/v1/resource/note/detail?id={note_id}")
    if not result:
        print(f"[WARN] 获取笔记详情失败: note_id={note_id}，请求未返回结果")
        return None
    if not result.get("success"):
        print(f"[WARN] 获取笔记详情失败: note_id={note_id}，message={result.get('message', '未知错误')}")
        return None
    data = result.get("data") or {}
    if isinstance(data, dict) and isinstance(data.get("note"), dict):
        return data.get("note") or {}
    return data


def get_note_id(note):
    return str(note.get("id") or note.get("note_id") or "")


def get_note_created_at(note):
    return note.get("created_at") or note.get("createdAt") or ""


def get_note_updated_at(note, note_detail=None):
    if note_detail:
        return (
            note_detail.get("updated_at")
            or note_detail.get("updatedAt")
            or note.get("updated_at")
            or note.get("updatedAt")
            or get_note_created_at(note)
        )
    return note.get("updated_at") or note.get("updatedAt") or get_note_created_at(note)


def parse_iso_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            # Get 笔记 API 返回的无时区时间为北京时间（UTC+8），不是 UTC
            return parsed.replace(tzinfo=TZ_CN)
        return parsed
    except Exception:
        return None


def filter_notes_by_time(notes, hours=None):
    """按最近更新时间筛选笔记。"""
    if hours is None:
        return notes

    now = datetime.now(timezone.utc)
    cutoff_time = now - timedelta(hours=hours)
    filtered = []

    for note in notes:
        note_time = parse_iso_datetime(get_note_updated_at(note))
        if note_time is None:
            filtered.append(note)
            continue
        if note_time >= cutoff_time:
            filtered.append(note)

    return filtered


def unique_list(items):
    result = []
    seen = set()
    for item in items:
        if not item:
            continue
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def get_nested_value(data, path):
    current = data
    for key in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def normalize_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [normalize_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        for key in ("content", "text", "plain_text", "markdown", "value", "excerpt", "summary", "original"):
            text = normalize_text(value.get(key))
            if text:
                return text
        parts = []
        for item in value.values():
            text = normalize_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    return str(value).strip()


def collapse_blank_lines(text):
    text = (text or "").replace("\r\n", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_inline_markdown(text):
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"!\[[^\]]*\]\(([^)]+)\)", "", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", cleaned)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"(\*\*|__)(.*?)\1", r"\2", cleaned)
    cleaned = re.sub(r"(\*|_)(.*?)\1", r"\2", cleaned)
    cleaned = re.sub(r"~~(.*?)~~", r"\1", cleaned)
    return cleaned.strip()


def is_probably_image_url(value):
    if not value:
        return False
    normalized = value.strip().lower()
    return bool(re.search(r"\.(png|jpe?g|gif|webp|svg|bmp|heic)(\?.*)?$", normalized))


def strip_non_text_media(text):
    cleaned = (text or "").replace("\r\n", "\n")
    cleaned = re.sub(r"<img\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"!\[[^\]]*\]\(([^)]+)\)", "", cleaned)

    filtered_lines = []
    for raw_line in cleaned.split("\n"):
        line = raw_line.strip()
        if not line:
            filtered_lines.append("")
            continue
        if is_probably_image_url(line):
            continue
        filtered_lines.append(raw_line)

    return collapse_blank_lines("\n".join(filtered_lines))


def normalize_ai_markdown(text):
    cleaned = strip_non_text_media(text)
    if not cleaned:
        return ""

    normalized_lines = []
    table_rows = []

    def flush_table_rows():
        nonlocal table_rows
        if not table_rows:
            return

        parsed_rows = []
        for row in table_rows:
            stripped_row = row.strip()
            if not (stripped_row.startswith("|") and stripped_row.endswith("|")):
                continue
            columns = [strip_inline_markdown(col.strip()) for col in stripped_row.strip("|").split("|")]
            if not any(columns):
                continue
            if all(re.fullmatch(r":?-{3,}:?", col.replace(" ", "")) for col in columns if col):
                continue
            parsed_rows.append(columns)

        if not parsed_rows:
            table_rows = []
            return

        header = parsed_rows[0]
        body_rows = parsed_rows[1:]
        if body_rows and len(header) > 1:
            for row in body_rows:
                label = row[0] if len(row) > 0 else ""
                detail = "；".join(part for part in row[1:] if part)
                merged = f"{label}：{detail}" if label and detail else label or detail
                if merged:
                    normalized_lines.append(f"- {merged}")
        else:
            for row in parsed_rows:
                merged = "；".join(part for part in row if part)
                if merged:
                    normalized_lines.append(f"- {merged}")

        table_rows = []

    for raw_line in cleaned.split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_table_rows()
            normalized_lines.append("")
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            table_rows.append(stripped)
            continue

        flush_table_rows()

        if re.fullmatch(r"[•●◦▪▫■□]+", stripped):
            continue

        heading_match = re.fullmatch(r"\*\*(.+?)\*\*[:：]?", stripped) or re.fullmatch(r"__(.+?)__[:：]?", stripped)
        if heading_match:
            heading_text = heading_match.group(1).strip()
            if heading_text:
                normalized_lines.append(f"## {strip_inline_markdown(heading_text)}")
                continue

        inline_heading_match = re.match(r"^(?:\*\*|__)(.+?)(?:\*\*|__)[:：]\s*(.+)$", stripped)
        if inline_heading_match:
            heading_text = strip_inline_markdown(inline_heading_match.group(1))
            body_text = strip_inline_markdown(inline_heading_match.group(2))
            if heading_text:
                normalized_lines.append(f"## {heading_text}")
            if body_text:
                normalized_lines.append(body_text)
            continue

        if not stripped.startswith("#") and len(stripped) <= 30 and stripped.endswith(("：", ":")):
            normalized_lines.append(f"## {strip_inline_markdown(stripped[:-1].strip())}")
            continue

        heading_mark_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading_mark_match:
            heading_level = min(len(heading_mark_match.group(1)), 3)
            heading_text = strip_inline_markdown(heading_mark_match.group(2))
            normalized_lines.append(f"{'#' * heading_level} {heading_text}")
            continue

        line = re.sub(r"^\s*[•●◦▪▫■□]\s+", "- ", line)
        line = re.sub(r"^(\s*)(\d+)[\)）、]\s+", r"\1\2. ", line)
        if re.match(r"^\s*[-*+]\s+", line):
            prefix, content = re.match(r"^(\s*[-*+]\s+)(.+)$", line).groups()
            normalized_lines.append(f"{prefix}{strip_inline_markdown(content)}")
            continue
        numbered_match = re.match(r"^(\s*\d+\.\s+)(.+)$", line)
        if numbered_match:
            normalized_lines.append(f"{numbered_match.group(1)}{strip_inline_markdown(numbered_match.group(2))}")
            continue

        normalized_lines.append(strip_inline_markdown(line))

    flush_table_rows()

    return collapse_blank_lines("\n".join(normalized_lines))


def ai_text_to_blocks(content):
    text = normalize_ai_markdown(content)
    if not text:
        return [paragraph_block("（无 AI 笔记）", color="gray")]

    blocks = []
    paragraph_lines = []
    current_list_type = None

    def flush_paragraph():
        nonlocal paragraph_lines
        if not paragraph_lines:
            return
        paragraph_text = "\n".join(paragraph_lines).strip()
        if paragraph_text:
            for chunk in chunk_text(paragraph_text):
                blocks.append(paragraph_block(chunk))
        paragraph_lines = []

    def flush_list():
        nonlocal current_list_type
        current_list_type = None

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            flush_list()
            continue

        heading_match = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading_match:
            flush_paragraph()
            flush_list()
            level = min(len(heading_match.group(1)), 3)
            blocks.append(heading_block(level, heading_match.group(2).strip()))
            continue

        if line.startswith("> "):
            flush_paragraph()
            flush_list()
            blocks.append(quote_block(line[2:].strip()))
            continue

        bullet_match = re.match(r"^[-*+]\s+(.+)$", line)
        if bullet_match:
            flush_paragraph()
            current_list_type = "bullet"
            blocks.append(bulleted_list_item_block(bullet_match.group(1).strip()))
            continue

        numbered_match = re.match(r"^(\d+)\.\s+(.+)$", line)
        if numbered_match:
            flush_paragraph()
            current_list_type = "numbered"
            blocks.append(numbered_list_item_block(numbered_match.group(2).strip()))
            continue

        label_value_match = re.match(r"^([^:：]{2,24})[:：]\s+(.+)$", line)
        if label_value_match and not line.startswith(("http://", "https://")):
            flush_paragraph()
            flush_list()
            label = strip_inline_markdown(label_value_match.group(1))
            value = strip_inline_markdown(label_value_match.group(2))
            if label:
                blocks.append(heading_block(3, label))
            if value:
                if "；" in value or "; " in value:
                    parts = [part.strip() for part in re.split(r"[；;]", value) if part.strip()]
                    for part in parts:
                        blocks.append(bulleted_list_item_block(part))
                else:
                    blocks.append(paragraph_block(value))
            continue

        if current_list_type and blocks:
            last_block = blocks[-1]
            block_type = "bulleted_list_item" if current_list_type == "bullet" else "numbered_list_item"
            if last_block.get("type") == block_type:
                rich_text = last_block[block_type].get("rich_text", [])
                if rich_text and rich_text[0].get("type") == "text":
                    previous = rich_text[0]["text"].get("content", "")
                    rich_text[0]["text"]["content"] = f"{previous}\n{strip_inline_markdown(line)}"
                    continue

        paragraph_lines.append(strip_inline_markdown(line))

    flush_paragraph()
    return blocks[:100]


def extract_names(value):
    names = []
    if isinstance(value, list):
        for item in value:
            names.extend(extract_names(item))
    elif isinstance(value, dict):
        for key in (
            "name",
            "title",
            "topic_name",
            "database_name",
            "notebook_name",
            "folder_name",
            "collection_name",
            "space_name",
        ):
            if value.get(key):
                names.append(str(value[key]).strip())
        for nested_key in ("topic", "database", "notebook", "folder", "collection", "space"):
            if nested_key in value:
                names.extend(extract_names(value[nested_key]))
    elif isinstance(value, str):
        names.append(value.strip())
    return unique_list(names)


def extract_tags(note, note_detail=None):
    """提取笔记标签，过滤掉 system 类型的自动生成标签。"""
    tags = []
    sources = [note]
    if note_detail:
        sources.append(note_detail)

    for source in sources:
        if not isinstance(source, dict):
            continue
        raw_tags = source.get("tags") or source.get("tag_list") or []
        if isinstance(raw_tags, list):
            for item in raw_tags:
                if isinstance(item, dict):
                    # 过滤 system 类型（如"AI链接笔记"等自动标签），只保留 ai 和用户标签
                    if item.get("type") == "system":
                        continue
                    name = item.get("name") or item.get("tag_name") or item.get("title")
                    if name:
                        tags.append(str(name).strip())
                elif isinstance(item, str):
                    tags.append(item.strip())

    return unique_list(tags)[:10]


def extract_knowledge_base(note, note_detail=None):
    """从 topics 字段提取知识库名称（Get 笔记 API 实际字段名）。"""
    for source in [note, note_detail]:
        if not isinstance(source, dict):
            continue
        topics = source.get("topics") or []
        if isinstance(topics, list) and topics:
            name = topics[0].get("name") or ""
            if name:
                return name.strip()
    return ""


def extract_original_content(note, note_detail):
    candidate_paths = (
        "original_content",
        "raw_content",
        "source_content",
        "source_text",
        "source_markdown",
        "origin_content",
        "origin_text",
        "note_content",
        "body",
        "full_text",
        "web_page.content",
        "audio.original",
        "transcript_original",
        "transcript_raw",
        "ocr_text",
    )

    for path in candidate_paths:
        text = normalize_text(get_nested_value(note_detail, path))
        if text:
            return strip_non_text_media(text)

    return ""


def extract_source_url(note, note_detail):
    # 优先取 web_page.url 和常见路径
    candidate_paths = (
        "web_page.url",
        "source_url",
        "origin_url",
        "url",
        "link",
    )
    for path in candidate_paths:
        value = normalize_text(get_nested_value(note_detail, path))
        if value:
            return value

    # 兜底：从 attachments 里取第一个 link 类型的 url
    attachments = (note_detail.get("attachments") or []) if isinstance(note_detail, dict) else []
    for att in attachments:
        if isinstance(att, dict) and att.get("url"):
            return str(att["url"]).strip()

    return ""


def extract_ai_note_content(note, note_detail):
    candidate_paths = (
        "ai_note",
        "ai_notes",
        "ai_content",
        "ai_summary",
        "summary",
        "excerpt",
        "web_page.excerpt",
        "content",
        "text",
        "transcript",
    )

    for path in candidate_paths:
        text = normalize_text(get_nested_value(note_detail, path))
        if text:
            return normalize_ai_markdown(text)

    return ""


def extract_append_sections(note_detail):
    """拉取追加笔记内容。

    Get 笔记 API 的追加笔记（追加笔记）以独立 note 形式存储，父笔记通过
    children_ids 字段记录子笔记 ID，子笔记的 is_child_note=True。
    需要对每个 child_id 单独调用 fetch_note_detail 获取正文。
    """
    if not isinstance(note_detail, dict):
        return []

    children_ids = note_detail.get("children_ids") or []
    if not children_ids:
        return []

    sections = []
    for index, child_id in enumerate(children_ids, 1):
        child = fetch_note_detail(str(child_id))
        if not child:
            continue
        content = (child.get("content") or "").strip()
        if not content:
            continue
        created_at = (child.get("created_at") or "").strip()
        title = f"追加笔记 {index}"
        if created_at:
            title = f"{title} · {created_at}"
        sections.append((title, content))

    return sections


def chunk_text(text, limit=1800):
    text = (text or "").strip()
    if not text:
        return []

    chunks = []
    current = ""
    for line in text.splitlines():
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = line

    if current:
        chunks.append(current)

    return chunks


def make_rich_text(text):
    return [{"type": "text", "text": {"content": text or ""}}]


def paragraph_block(text, color="default"):
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": make_rich_text(text),
            "color": color,
        },
    }


def divider_block():
    return {"object": "block", "type": "divider", "divider": {}}


def heading_block(level, text):
    block_type = f"heading_{level}"
    return {
        "object": "block",
        "type": block_type,
        block_type: {
            "rich_text": make_rich_text(text),
            "is_toggleable": False,
            "color": "default",
        },
    }


def toggleable_heading_block(level, text, children=None):
    block_type = f"heading_{level}"
    heading_data = {
        "rich_text": make_rich_text(text),
        "is_toggleable": True,
        "color": "default",
    }
    if children:
        heading_data["children"] = children[:100]
    return {
        "object": "block",
        "type": block_type,
        block_type: heading_data,
    }


def bulleted_list_item_block(text):
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": make_rich_text(text),
            "color": "default",
        },
    }


def numbered_list_item_block(text):
    return {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {
            "rich_text": make_rich_text(text),
            "color": "default",
        },
    }


def quote_block(text):
    return {
        "object": "block",
        "type": "quote",
        "quote": {
            "rich_text": make_rich_text(text),
            "color": "default",
        },
    }


def code_block(text, language="plain text"):
    supported_languages = {
        "abap", "arduino", "bash", "basic", "c", "clojure", "coffeescript", "c++",
        "c#", "css", "dart", "diff", "docker", "elixir", "elm", "erlang", "flow",
        "fortran", "f#", "gherkin", "glsl", "go", "graphql", "groovy", "haskell",
        "html", "java", "javascript", "json", "julia", "kotlin", "latex", "less",
        "lisp", "livescript", "lua", "makefile", "markdown", "markup", "matlab",
        "mermaid", "nix", "objective-c", "ocaml", "pascal", "perl", "php", "plain text",
        "powershell", "prolog", "protobuf", "python", "r", "reason", "ruby", "rust",
        "sass", "scala", "scheme", "scss", "shell", "sql", "swift", "typescript",
        "vb.net", "verilog", "vhdl", "visual basic", "webassembly", "xml", "yaml", "java/c/c++/c#"
    }
    language = (language or "plain text").strip().lower()
    if language not in supported_languages:
        language = "plain text"
    return {
        "object": "block",
        "type": "code",
        "code": {
            "rich_text": make_rich_text(text),
            "caption": [],
            "language": language,
        },
    }


def markdown_to_blocks(content):
    text = (content or "").replace("\r\n", "\n").strip()
    if not text:
        return [paragraph_block("（无内容）", color="gray")]

    blocks = []
    paragraph_lines = []
    code_lines = []
    in_code_block = False
    code_language = "plain text"

    def flush_paragraph():
        nonlocal paragraph_lines
        if not paragraph_lines:
            return
        paragraph_text = "\n".join(paragraph_lines).strip()
        if paragraph_text:
            for chunk in chunk_text(paragraph_text):
                blocks.append(paragraph_block(chunk))
        paragraph_lines = []

    def flush_code():
        nonlocal code_lines, code_language
        code_text = "\n".join(code_lines).strip("\n")
        if code_text:
            for chunk in chunk_text(code_text):
                blocks.append(code_block(chunk, code_language))
        code_lines = []
        code_language = "plain text"

    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_paragraph()
            if in_code_block:
                flush_code()
                in_code_block = False
            else:
                in_code_block = True
                language = stripped[3:].strip()
                if language:
                    code_language = language
            continue

        if in_code_block:
            code_lines.append(raw_line)
            continue

        if not stripped:
            flush_paragraph()
            continue

        if stripped == "---" or stripped == "***":
            flush_paragraph()
            blocks.append(divider_block())
            continue

        if stripped.startswith("#"):
            flush_paragraph()
            heading_marks = len(stripped) - len(stripped.lstrip("#"))
            if 1 <= heading_marks <= 3 and stripped[heading_marks:heading_marks + 1] == " ":
                blocks.append(heading_block(heading_marks, stripped[heading_marks + 1:].strip()))
                continue

        if stripped.startswith("> "):
            flush_paragraph()
            quote_text = stripped[2:].strip()
            if quote_text:
                blocks.append(quote_block(quote_text))
                continue

        bullet_prefixes = ("- ", "* ", "+ ")
        if stripped.startswith(bullet_prefixes):
            flush_paragraph()
            blocks.append(bulleted_list_item_block(stripped[2:].strip()))
            continue

        numbered_marker, dot, remainder = stripped.partition(". ")
        if dot and numbered_marker.isdigit():
            flush_paragraph()
            blocks.append(numbered_list_item_block(remainder.strip()))
            continue

        paragraph_lines.append(line)

    flush_paragraph()
    if in_code_block:
        flush_code()

    return blocks[:100]


def readable_text_to_blocks(content):
    text = (content or "").replace("\r\n", "\n").strip()
    if not text:
        return [paragraph_block("（未获取到原文）", color="gray")]

    blocks = []
    paragraph_lines = []

    def flush_paragraph():
        nonlocal paragraph_lines
        if not paragraph_lines:
            return
        paragraph_text = "\n".join(paragraph_lines).strip()
        if paragraph_text:
            for chunk in chunk_text(paragraph_text):
                blocks.append(paragraph_block(chunk))
        paragraph_lines = []

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            continue

        bullet_prefixes = ("- ", "* ", "+ ", "• ")
        if line.startswith(bullet_prefixes):
            flush_paragraph()
            blocks.append(bulleted_list_item_block(line[2:].strip()))
            continue

        numbered_marker, dot, remainder = line.partition(". ")
        if dot and numbered_marker.isdigit():
            flush_paragraph()
            blocks.append(numbered_list_item_block(remainder.strip()))
            continue

        paragraph_lines.append(line)

    flush_paragraph()
    return blocks[:100]


def section_heading_block(level, title, content, render_mode="markdown"):
    if render_mode == "plain":
        child_blocks = readable_text_to_blocks(content)
    elif render_mode == "ai":
        child_blocks = ai_text_to_blocks(content)
    else:
        child_blocks = markdown_to_blocks(content)
    if not child_blocks:
        child_blocks = [paragraph_block("（无内容）", color="gray")]
    return toggleable_heading_block(level, title, child_blocks)


def build_notion_payload(note, note_detail):
    """构建 Notion 页面属性与内容。"""
    note_id = get_note_id(note)
    created_at = get_note_created_at(note)
    updated_at = get_note_updated_at(note, note_detail if isinstance(note_detail, dict) else None)
    title = (note.get("title") or "").strip()

    knowledge_base = extract_knowledge_base(note, note_detail)
    note_type = note.get("note_type") or (note_detail.get("note_type") if isinstance(note_detail, dict) else None) or "other"
    tags = extract_tags(note, note_detail)
    original_content = extract_original_content(note, note_detail)
    ai_content = extract_ai_note_content(note, note_detail)
    append_sections = extract_append_sections(note_detail)
    source_url = extract_source_url(note, note_detail)

    if not title:
        seed_text = ai_content or original_content
        title = seed_text[:50] + "..." if len(seed_text) > 50 else seed_text
    if not title:
        title = f"Get笔记 {note_id[:8]}"

    properties = {
        "标题": {
            "title": [{"text": {"content": title[:100]}}]
        },
        "笔记 ID": {
            "rich_text": [{"text": {"content": note_id}}]
        },
        "笔记类型": {
            "select": {"name": note_type[:100]}
        },
    }

    if created_at:
        created_dt = parse_iso_datetime(created_at)
        if created_dt:
            properties["创建时间"] = {"date": {"start": created_dt.strftime("%Y-%m-%d")}}

    if updated_at:
        updated_dt = parse_iso_datetime(updated_at)
        if updated_dt:
            # 存储 Get 笔记侧的最后更新时间，用于下次运行时检测内容变更
            properties["笔记更新时间"] = {"date": {"start": updated_dt.isoformat()}}

    # 存储追加笔记数量，父笔记 updated_at 不随追加笔记变化，需靠此字段检测新增
    children_count = note_detail.get("children_count", 0) if isinstance(note_detail, dict) else 0
    properties["追加笔记数"] = {"number": int(children_count)}

    if tags:
        properties["标签"] = {"multi_select": [{"name": tag} for tag in tags]}

    if knowledge_base:
        properties["知识库"] = {"select": {"name": knowledge_base[:100]}}

    if source_url:
        properties["原文链接"] = {"url": source_url}

    append_children = []
    for append_title, content in append_sections:
        append_children.append(section_heading_block(3, append_title[:100], content))
    if not append_children:
        append_children = [paragraph_block("（无追加笔记）", color="gray")]

    children = [
        section_heading_block(2, "原文", original_content, render_mode="plain"),
        section_heading_block(2, "AI 笔记", ai_content, render_mode="ai"),
        toggleable_heading_block(2, "追加笔记", append_children),
    ]

    return properties, children[:100]


def query_notion_page_by_note_id(note_id):
    body = {
        "filter": {
            "property": "笔记 ID",
            "rich_text": {"equals": note_id},
        },
        "page_size": 1,
    }
    result = notion_request(f"/databases/{NOTION_DB_ID}/query", body)
    if not result:
        return None
    pages = result.get("results") or []
    return pages[0] if pages else None


def fetch_notion_synced_note_state():
    """分页查询 Notion 数据库，返回所有已同步笔记的状态快照。

    返回格式: {note_id: {"updated_at": str, "children_count": int}}

    用于追加笔记检测：
    - updated_at 变化 → 内容有更新
    - children_count 增加 → 新增了追加笔记（父笔记 updated_at 不变，只能靠此字段）
    """
    note_state = {}
    start_cursor = None

    while True:
        body = {"page_size": 100}
        if start_cursor:
            body["start_cursor"] = start_cursor

        result = notion_request(f"/databases/{NOTION_DB_ID}/query", body)
        if not result:
            break

        for page in result.get("results") or []:
            props = page.get("properties") or {}

            # 取笔记 ID
            note_id_rt = (props.get("笔记 ID") or {}).get("rich_text") or []
            note_id = note_id_rt[0].get("plain_text", "") if note_id_rt else ""
            if not note_id:
                continue

            # 取已存储的 updated_at
            updated_at = ((props.get("笔记更新时间") or {}).get("date") or {}).get("start", "")

            # 取已存储的 children_count（追加笔记数）
            children_count = (props.get("追加笔记数") or {}).get("number")
            if children_count is None:
                children_count = -1  # -1 表示尚未记录，任何真实值都会触发更新

            note_state[note_id] = {
                "updated_at": updated_at,
                "children_count": int(children_count),
            }

        if not result.get("has_more"):
            break
        start_cursor = result.get("next_cursor")

    return note_state


def ensure_database_properties():
    """确保 Notion 数据库包含所有必要的属性字段，缺失时自动创建。"""
    required = {
        "笔记更新时间": {"date": {}},
        "追加笔记数": {"number": {}},
    }
    db = notion_request(f"/databases/{NOTION_DB_ID}", method="GET")
    if not db:
        print("[WARN] 无法读取 Notion 数据库结构，跳过属性初始化")
        return
    existing = set(db.get("properties", {}).keys())
    missing = {k: v for k, v in required.items() if k not in existing}
    if not missing:
        return
    print(f"[INFO] 自动创建缺失的数据库属性: {list(missing.keys())}")
    res = notion_request(f"/databases/{NOTION_DB_ID}", {"properties": missing}, method="PATCH")
    if res:
        print("[INFO] 数据库属性创建成功 ✓")
    else:
        print("[WARN] 数据库属性创建失败，「笔记更新时间」将无法写入")


def list_block_children(block_id):
    result = notion_request(f"/blocks/{block_id}/children?page_size=100", method="GET")
    if not result:
        return []
    return result.get("results") or []


def archive_block(block_id):
    return notion_request(f"/blocks/{block_id}", {"archived": True}, method="PATCH")


def append_block_children(block_id, children):
    for index in range(0, len(children), 50):
        batch = children[index:index + 50]
        result = notion_request(f"/blocks/{block_id}/children", {"children": batch}, method="PATCH")
        if not result:
            return False
    return True


def create_notion_page(properties, children):
    body = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": properties,
        "children": children,
    }
    return notion_request("/pages", body)


def archive_notion_page(page_id):
    return notion_request(f"/pages/{page_id}", {"archived": True}, method="PATCH")


def update_notion_page(page_id, properties, children):
    update_result = notion_request(f"/pages/{page_id}", {"properties": properties}, method="PATCH")
    if not update_result:
        return None

    for block in list_block_children(page_id):
        if block.get("archived"):
            continue
        archive_block(block.get("id"))

    if not append_block_children(page_id, children):
        return None

    return update_result


def sync_note(note, synced_ids):
    """同步单条笔记到 Notion。状态完全依赖 Notion 反查，synced_ids 仅用于单次运行内去重。"""
    note_id = get_note_id(note)
    note_detail = fetch_note_detail(note_id)
    if not note_detail:
        print(f"   ⚠️ 详情缺失，使用列表数据继续同步: {note.get('title', '')[:30] or '(无标题)'}...")
        note_detail = note

    existing_page = query_notion_page_by_note_id(note_id)
    page_id = existing_page.get("id") if existing_page else None

    # 门控：只同步已归入知识库（topics 不为空）的笔记
    knowledge_base = extract_knowledge_base(note, note_detail)
    if not knowledge_base:
        if page_id:
            archived = archive_notion_page(page_id)
            if archived:
                synced_ids.add(note_id)
                print(f"   🗂️ 已移出知识库，归档: {note.get('title', '')[:30] or '(无标题)'}...")
                return True, note_id
            print(f"   ❌ 归档失败: {note.get('title', '')[:30] or '(无标题)'}...")
            return False, note_id
        print(f"   ⏭️ 未归入知识库，跳过: {note.get('title', '')[:30] or '(无标题)'}...")
        synced_ids.add(note_id)
        return True, note_id

    properties, children = build_notion_payload(note, note_detail)

    if page_id:
        result = update_notion_page(page_id, properties, children)
        action = "更新"
        if not result:
            result = create_notion_page(properties, children)
            action = "重建"
    else:
        result = create_notion_page(properties, children)
        action = "创建"

    if result and result.get("id"):
        synced_ids.add(note_id)
        print(f"   ✅ {action}成功: {note.get('title', '')[:30] or '(无标题)'}...")
        return True, note_id

    print(f"   ❌ {action}失败: {note.get('title', '')[:30] or '(无标题)'}...")
    return False, note_id


# ============ 主流程 ============

def main():
    print("=" * 60)
    print("🚀 Get 笔记 → Notion 知识库同步开始")
    print("=" * 60)

    if not GETNOTE_API_KEY:
        print("[ERROR] GETNOTE_API_KEY 未配置")
        sys.exit(1)
    if not GETNOTE_CLIENT_ID:
        print("[ERROR] GETNOTE_CLIENT_ID 未配置")
        sys.exit(1)
    if not NOTION_TOKEN:
        print("[ERROR] NOTION_TOKEN 未配置")
        sys.exit(1)
    if not NOTION_DB_ID:
        print("[ERROR] NOTION_DB_ID 未配置")
        sys.exit(1)

    # 确保数据库 schema 包含所有必要属性（如「笔记更新时间」）
    ensure_database_properties()

    synced_ids = set()

    print("[INFO] 正在拉取 Get 笔记...")
    all_notes = fetch_getnote_notes()
    if not all_notes:
        print("[INFO] 没有获取到笔记，跳过")
        return

    # ── 第一轮：最近 CHECK_HOURS 内 updated_at 有变化的笔记 ──────────────
    recent_notes = filter_notes_by_time(all_notes, CHECK_HOURS)
    print(f"[INFO] 最近 {CHECK_HOURS} 小时内有更新的笔记: {len(recent_notes)} 条")

    candidate_notes = []
    seen_note_ids = set()
    for note in recent_notes:
        note_id = get_note_id(note)
        if note_id in seen_note_ids:
            continue
        seen_note_ids.add(note_id)
        candidate_notes.append(note)

    # ── 第二轮：追加笔记检测 ─────────────────────────────────────────────
    # Get笔记 API 的追加笔记（children）不会更新父笔记的 updated_at，
    # 因此必须同时比较两个维度：
    #   1. updated_at 变化 → 内容有更新
    #   2. children_count 增加 → 新增了追加笔记
    # 仅对「第一轮未覆盖」且「已在 Notion 中」的笔记做检测。
    print("[INFO] 检查已同步笔记是否有新追加内容（追加笔记检测）...")
    notion_state = fetch_notion_synced_note_state()
    append_catch_count = 0

    # 建立 note_id -> note 映射，方便快速查找
    note_map = {get_note_id(n): n for n in all_notes}

    for note_id, stored in notion_state.items():
        if note_id in seen_note_ids:
            continue  # 第一轮已处理
        note = note_map.get(note_id)
        if not note:
            continue  # Get笔记侧已不存在该笔记

        needs_update = False

        # 检测 1：updated_at 是否变化
        current_updated_str = get_note_updated_at(note)
        current_dt = parse_iso_datetime(current_updated_str)
        stored_dt = parse_iso_datetime(stored.get("updated_at", ""))
        if current_dt and stored_dt and current_dt > stored_dt:
            needs_update = True

        # 检测 2：追加笔记数是否增加（父笔记 updated_at 不变，靠此字段兜底）
        if not needs_update:
            current_children = int(note.get("children_count", 0))
            stored_children = int(stored.get("children_count", -1))
            if stored_children >= 0 and current_children > stored_children:
                needs_update = True

        if needs_update:
            seen_note_ids.add(note_id)
            candidate_notes.append(note)
            append_catch_count += 1

    if append_catch_count:
        print(f"[INFO] 追加笔记检测额外发现需更新的笔记: {append_catch_count} 条")

    print(f"[INFO] 本轮共需处理的笔记: {len(candidate_notes)} 条")

    if not candidate_notes:
        print("✨ 没有新的笔记需要同步")
        return

    success_count = 0

    for index, note in enumerate(candidate_notes, 1):
        note_title = note.get("title", "")[:30] or "(无标题)"
        print(f"\n[{index}/{len(candidate_notes)}] 正在同步: {note_title}...")

        success, _ = sync_note(note, synced_ids)
        if success:
            success_count += 1

    print("\n" + "=" * 60)
    print("🎉 同步完成!")
    print(f"   本次尝试: {len(candidate_notes)} 条")
    print(f"   同步成功: {success_count} 条")
    print(f"   同步失败: {len(candidate_notes) - success_count} 条")
    print("=" * 60)

    if success_count < len(candidate_notes):
        sys.exit(1)


if __name__ == "__main__":
    main()
