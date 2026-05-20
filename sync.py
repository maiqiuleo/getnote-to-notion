#!/usr/bin/env python3
"""
Get 笔记 → Notion get 笔记数据库 自动同步

功能：
- 仅同步已归入 Get 数据库/知识库的笔记
- 按最近更新时间检查需要同步的笔记
- 首次创建，后续更新（支持 AI 笔记/追加笔记变更后重新同步）
- 保留标签、知识库分类、创建时间等元数据
"""

import json
import os
import ssl
import sys
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

# ============ 配置 ============
GETNOTE_API_KEY = os.environ.get("GETNOTE_API_KEY", "")
GETNOTE_CLIENT_ID = os.environ.get("GETNOTE_CLIENT_ID", "cli_3802f9db08b811f197679c63c078bacc")

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_VERSION = "2022-06-28"
NOTION_DB_ID = os.environ.get("NOTION_DB_ID", "")

SYNC_STATE_FILE = os.path.join(os.path.dirname(__file__), "processed_ids.json")
CHECK_HOURS = int(os.environ.get("CHECK_HOURS", "2"))
SYNC_LAYOUT_VERSION = 2

TZ_CN = timezone(timedelta(hours=8))


# ============ 辅助函数 ============

def load_sync_state():
    """加载同步状态，兼容旧版 list 结构。"""
    try:
        if not os.path.exists(SYNC_STATE_FILE):
            return {}

        with open(SYNC_STATE_FILE, "r") as f:
            data = json.load(f)

        if isinstance(data, list):
            # 兼容旧版：仅记录已同步 ID
            return {str(note_id): {"legacy": True} for note_id in data}
        if isinstance(data, dict):
            return data
    except Exception as e:
        print(f"[WARN] 加载同步状态失败: {e}")

    return {}


def save_sync_state(sync_state):
    """保存同步状态。"""
    try:
        with open(SYNC_STATE_FILE, "w") as f:
            json.dump(sync_state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERROR] 保存同步状态失败: {e}")


def getnote_request(path, method="GET", body=None):
    """发送 Get 笔记 API 请求。"""
    url = f"https://openapi.biji.com{path}"
    data = json.dumps(body).encode() if body is not None else None

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
        error_body = e.read().decode()[:800]
        print(f"[ERROR] Get笔记 API error {e.code}: {error_body}")
        return None
    except Exception as e:
        print(f"[ERROR] Get笔记 request failed: {e}")
        return None


def notion_request(path, body=None, method="POST"):
    """发送 Notion API 请求。"""
    url = f"https://api.notion.com/v1{path}"
    data = json.dumps(body).encode() if body is not None else None

    req = Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {NOTION_TOKEN}")
    req.add_header("Notion-Version", NOTION_VERSION)
    req.add_header("Content-Type", "application/json")

    ctx = ssl.create_default_context()
    try:
        with urlopen(req, context=ctx, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        error_body = e.read().decode()[:1200]
        print(f"[ERROR] Notion API error {e.code}: {error_body}")
        return None
    except Exception as e:
        print(f"[ERROR] Notion request failed: {e}")
        return None


def fetch_getnote_notes():
    """从 Get 笔记拉取笔记列表。"""
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

        notes = result.get("data", {}).get("notes", [])
        if not notes:
            break

        all_notes.extend(notes)
        next_since_id = notes[-1].get("id", 0)
        if next_since_id == since_id:
            break

        since_id = next_since_id
        iteration += 1

        if len(notes) < 20:
            break

    print(f"[INFO] 共获取 {len(all_notes)} 条笔记")
    return all_notes


def fetch_note_detail(note_id):
    """获取单条笔记详情。"""
    result = getnote_request(f"/open/api/v1/resource/note/detail?note_id={note_id}")
    if not result or not result.get("success"):
        return None
    return result.get("data") or {}


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
            return parsed.replace(tzinfo=timezone.utc)
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


def extract_topic_names(note, note_detail=None):
    """尽量从多种字段中推断 Get 中所属的数据库/知识库名称。"""
    sources = [note]
    if note_detail:
        sources.append(note_detail)

    names = []
    candidate_keys = (
        "topics",
        "topic_list",
        "databases",
        "database_list",
        "notebooks",
        "folders",
        "collections",
        "spaces",
    )
    direct_keys = (
        "topic_name",
        "database_name",
        "notebook_name",
        "folder_name",
        "collection_name",
        "space_name",
    )

    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in candidate_keys:
            if key in source:
                names.extend(extract_names(source.get(key)))
        for key in direct_keys:
            value = source.get(key)
            if value:
                names.append(str(value).strip())

    return unique_list(names)


def is_note_in_database(note, note_detail=None):
    return bool(extract_topic_names(note, note_detail))


def extract_tags(note, note_detail=None):
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
                    name = item.get("name") or item.get("tag_name") or item.get("title")
                    if name:
                        tags.append(str(name).strip())
                elif isinstance(item, str):
                    tags.append(item.strip())

    return unique_list(tags)[:10]


def extract_original_content(note, note_detail):
    candidate_paths = (
        "original_content",
        "raw_content",
        "source_content",
        "web_page.content",
        "audio.original",
        "audio.transcript",
        "transcript_original",
        "transcript_raw",
        "ocr_text",
        "text",
        "transcript",
        "content",
    )

    for path in candidate_paths:
        text = normalize_text(get_nested_value(note_detail, path))
        if text:
            return text

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
            return text

    return ""


def extract_append_sections(note_detail):
    """递归寻找追加笔记/补充内容。"""
    append_sections = []
    candidate_keys = {
        "append_notes",
        "appends",
        "append_note_list",
        "supplements",
        "follow_ups",
        "updates",
        "child_notes",
    }
    title_keys = ("title", "name", "label")
    content_keys = (
        "content",
        "text",
        "summary",
        "excerpt",
        "original_content",
        "raw_content",
    )

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in candidate_keys and item:
                    append_sections.extend(normalize_append_items(item))
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    def normalize_append_items(items):
        normalized = []
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            return normalized

        for index, item in enumerate(items, 1):
            if isinstance(item, str):
                text = item.strip()
                if text:
                    normalized.append((f"追加笔记 {index}", text))
                continue

            if not isinstance(item, dict):
                text = normalize_text(item)
                if text:
                    normalized.append((f"追加笔记 {index}", text))
                continue

            title = ""
            for key in title_keys:
                title = normalize_text(item.get(key))
                if title:
                    break
            if not title:
                created_at = normalize_text(item.get("created_at") or item.get("updated_at"))
                title = f"追加笔记 {index}"
                if created_at:
                    title = f"{title} ({created_at})"

            content = ""
            for key in content_keys:
                content = normalize_text(item.get(key))
                if content:
                    break

            if not content:
                content = normalize_text(item)

            if content:
                normalized.append((title, content))

        return normalized

    walk(note_detail)

    deduped = []
    seen = set()
    for title, content in append_sections:
        signature = f"{title}\n{content}"
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append((title, content))

    return deduped


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


def paragraph_block(text, color="default"):
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": text}}],
            "color": color,
        },
    }


def divider_block():
    return {"object": "block", "type": "divider", "divider": {}}


def toggle_block(title, content):
    child_blocks = [paragraph_block(chunk) for chunk in chunk_text(content)]
    if not child_blocks:
        child_blocks = [paragraph_block("（无内容）", color="gray")]
    return {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": [{"type": "text", "text": {"content": title}}],
            "children": child_blocks[:100],
            "color": "default",
        },
    }


def build_notion_payload(note, note_detail):
    """构建 Notion 页面属性与内容。"""
    note_id = get_note_id(note)
    created_at = get_note_created_at(note)
    updated_at = get_note_updated_at(note, note_detail)
    title = (note.get("title") or "").strip()

    topic_names = extract_topic_names(note, note_detail)
    tags = extract_tags(note, note_detail)
    original_content = extract_original_content(note, note_detail)
    ai_content = extract_ai_note_content(note, note_detail)
    append_sections = extract_append_sections(note_detail)

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
            "select": {"name": "plain_text"}
        },
    }

    if created_at:
        created_dt = parse_iso_datetime(created_at)
        if created_dt:
            properties["创建时间"] = {"date": {"start": created_dt.strftime("%Y-%m-%d")}}

    if tags:
        properties["标签"] = {"multi_select": [{"name": tag} for tag in tags]}

    if topic_names:
        properties["知识库"] = {"select": {"name": topic_names[0][:100]}}

    meta_parts = ["来源: Get笔记"]
    if created_at:
        meta_parts.append(f"创建时间: {created_at}")
    if updated_at and updated_at != created_at:
        meta_parts.append(f"更新时间: {updated_at}")
    if topic_names:
        meta_parts.append(f"知识库: {', '.join(topic_names)}")
    if tags:
        meta_parts.append(f"标签: {', '.join(tags)}")

    children = [
        paragraph_block(" | ".join(meta_parts), color="gray"),
        divider_block(),
        toggle_block("原文", original_content or ai_content),
    ]

    if ai_content and ai_content != original_content:
        children.append(toggle_block("AI 笔记", ai_content))

    for title, content in append_sections:
        children.append(toggle_block(title[:100], content))

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


def update_notion_page(page_id, properties, children):
    update_result = notion_request(f"/pages/{page_id}", {"properties": properties}, method="PATCH")
    if not update_result:
        return None

    for block in list_block_children(page_id):
        archive_block(block.get("id"))

    if not append_block_children(page_id, children):
        return None

    return update_result


def needs_sync(note, sync_state):
    note_id = get_note_id(note)
    entry = sync_state.get(note_id, {})
    note_updated_at = get_note_updated_at(note)

    if not entry:
        return True
    if entry.get("layout_version") != SYNC_LAYOUT_VERSION:
        return True
    if note_updated_at and entry.get("updated_at") != note_updated_at:
        return True
    return False


def sync_note(note, sync_state):
    note_id = get_note_id(note)
    note_detail = fetch_note_detail(note_id)
    if not note_detail:
        print(f"   ❌ 获取详情失败: {note.get('title', '')[:30] or '(无标题)'}...")
        return False, note_id

    if not is_note_in_database(note, note_detail):
        print(f"   ⏭️ 未归入数据库，跳过: {note.get('title', '')[:30] or '(无标题)'}...")
        return True, note_id

    properties, children = build_notion_payload(note, note_detail)
    state_entry = sync_state.get(note_id, {})
    page_id = state_entry.get("page_id")

    if not page_id:
        existing_page = query_notion_page_by_note_id(note_id)
        if existing_page:
            page_id = existing_page.get("id")

    if page_id:
        result = update_notion_page(page_id, properties, children)
        action = "更新"
    else:
        result = create_notion_page(properties, children)
        action = "创建"

    if result and result.get("id"):
        sync_state[note_id] = {
            "page_id": result.get("id"),
            "updated_at": get_note_updated_at(note, note_detail),
            "layout_version": SYNC_LAYOUT_VERSION,
            "synced_at": datetime.now(TZ_CN).isoformat(),
        }
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
    if not NOTION_TOKEN:
        print("[ERROR] NOTION_TOKEN 未配置")
        sys.exit(1)
    if not NOTION_DB_ID:
        print("[ERROR] NOTION_DB_ID 未配置")
        sys.exit(1)

    sync_state = load_sync_state()
    print(f"[INFO] 已有同步记录: {len(sync_state)} 条")

    print("[INFO] 正在拉取 Get 笔记...")
    all_notes = fetch_getnote_notes()
    if not all_notes:
        print("[INFO] 没有获取到笔记，跳过")
        return

    recent_notes = filter_notes_by_time(all_notes, CHECK_HOURS)
    print(f"[INFO] 最近 {CHECK_HOURS} 小时内有更新的笔记: {len(recent_notes)} 条")

    layout_refresh_notes = []
    for note in all_notes:
        note_id = get_note_id(note)
        state_entry = sync_state.get(note_id, {})
        if state_entry and state_entry.get("layout_version") != SYNC_LAYOUT_VERSION:
            layout_refresh_notes.append(note)

    if layout_refresh_notes:
        print(f"[INFO] 需要重建新结构的旧笔记: {len(layout_refresh_notes)} 条")

    candidate_notes = []
    seen_note_ids = set()
    for note in recent_notes + layout_refresh_notes:
        note_id = get_note_id(note)
        if note_id in seen_note_ids:
            continue
        seen_note_ids.add(note_id)
        candidate_notes.append(note)

    notes_to_sync = [note for note in candidate_notes if needs_sync(note, sync_state)]
    print(f"[INFO] 需要创建/更新的笔记: {len(notes_to_sync)} 条")

    if not notes_to_sync:
        print("✨ 没有新的笔记需要同步")
        return

    success_count = 0

    for index, note in enumerate(notes_to_sync, 1):
        note_title = note.get("title", "")[:30] or "(无标题)"
        print(f"\n[{index}/{len(notes_to_sync)}] 正在同步: {note_title}...")

        success, _ = sync_note(note, sync_state)
        if success:
            success_count += 1

        if index % 5 == 0:
            save_sync_state(sync_state)

    save_sync_state(sync_state)

    print("\n" + "=" * 60)
    print("🎉 同步完成!")
    print(f"   本次尝试: {len(notes_to_sync)} 条")
    print(f"   同步成功: {success_count} 条")
    print(f"   同步失败: {len(notes_to_sync) - success_count} 条")
    print(f"   累计同步记录: {len(sync_state)} 条")
    print("=" * 60)

    if success_count < len(notes_to_sync):
        sys.exit(1)


if __name__ == "__main__":
    main()
