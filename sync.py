#!/usr/bin/env python3
"""
Get 笔记 → Notion 碎片中心 自动同步

功能：
- 拉取 Get 笔记中指定时间范围内的所有笔记
- 同步到 Notion 碎片中心数据库
- 避免重复同步（基于 Get 笔记 ID 记录）
- 支持录音转文字内容的同步
- 保留标签、来源链接等元数据

触发方式：
- GitHub Schedule：每 10 分钟检查一次
- repository_dispatch：外部触发（推荐，更稳定）
- 手动触发
"""

import json
import os
import sys
import ssl
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# ============ 配置 ============
GETNOTE_API_KEY = os.environ.get("GETNOTE_API_KEY", "")
GETNOTE_CLIENT_ID = os.environ.get("GETNOTE_CLIENT_ID", "cli_3802f9db08b811f197679c63c078bacc")

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_VERSION = "2022-06-28"
NOTION_DB_ID = os.environ.get("NOTION_DB_ID", "")  # 碎片中心数据库 ID

# 处理记录文件
PROCESSED_IDS_FILE = os.path.join(os.path.dirname(__file__), "processed_ids.json")

# 检查时间范围（默认检查过去2小时的笔记）
CHECK_HOURS = int(os.environ.get("CHECK_HOURS", "2"))

# 中国时区
TZ_CN = timezone(timedelta(hours=8))


# ============ 辅助函数 ============

def load_processed_ids():
    """加载已处理的笔记 ID"""
    try:
        if os.path.exists(PROCESSED_IDS_FILE):
            with open(PROCESSED_IDS_FILE, 'r') as f:
                return set(json.load(f))
    except Exception as e:
        print(f"[WARN] 加载已处理记录失败: {e}")
    return set()


def save_processed_ids(ids_set):
    """保存已处理的笔记 ID"""
    try:
        with open(PROCESSED_IDS_FILE, 'w') as f:
            json.dump(list(ids_set), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERROR] 保存已处理记录失败: {e}")


def getnote_request(path, method="GET", body=None):
    """发送 Get 笔记 API 请求"""
    url = f"https://openapi.biji.com{path}"
    data = json.dumps(body).encode() if body else None
    
    req = Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {GETNOTE_API_KEY}")
    req.add_header("x-client-id", GETNOTE_CLIENT_ID)
    req.add_header("Content-Type", "application/json")
    
    ctx = ssl.create_default_context()
    try:
        with urlopen(req, context=ctx, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        error_body = e.read().decode()[:500]
        print(f"[ERROR] Get笔记 API error {e.code}: {error_body}")
        return None
    except Exception as e:
        print(f"[ERROR] Get笔记 request failed: {e}")
        return None


def notion_request(path, body=None, method="POST"):
    """发送 Notion API 请求"""
    url = f"https://api.notion.com/v1{path}"
    data = json.dumps(body).encode() if body else None
    
    req = Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {NOTION_TOKEN}")
    req.add_header("Notion-Version", NOTION_VERSION)
    req.add_header("Content-Type", "application/json")
    
    ctx = ssl.create_default_context()
    try:
        with urlopen(req, context=ctx, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        error_body = e.read().decode()[:500]
        print(f"[ERROR] Notion API error {e.code}: {error_body}")
        return None
    except Exception as e:
        print(f"[ERROR] Notion request failed: {e}")
        return None


def fetch_getnote_notes():
    """
    从 Get 笔记拉取笔记列表
    使用分页获取所有笔记
    """
    all_notes = []
    page = 1
    page_size = 50
    max_pages = 10  # 最多获取 500 条笔记
    
    while page <= max_pages:
        print(f"[INFO] 正在获取 Get 笔记第 {page} 页...")
        
        result = getnote_request(
            f"/open/api/v1/resource/notes?page={page}&page_size={page_size}"
        )
        
        if not result or not result.get("success"):
            error_msg = result.get("message", "未知错误") if result else "请求失败"
            print(f"[ERROR] 获取笔记列表失败: {error_msg}")
            break
        
        notes = result.get("data", {}).get("list", [])
        if not notes:
            break
        
        all_notes.extend(notes)
        
        # 检查是否还有下一页
        total = result.get("data", {}).get("total", 0)
        if page * page_size >= total:
            break
        
        page += 1
    
    print(f"[INFO] 共获取 {len(all_notes)} 条笔记")
    return all_notes


def fetch_note_detail(note_id):
    """
    获取单条笔记的详细内容
    """
    result = getnote_request(f"/open/api/v1/resource/note/{note_id}")
    
    if not result or not result.get("success"):
        return None
    
    return result.get("data")


def filter_notes_by_time(notes, hours=None):
    """
    筛选指定时间范围内的笔记
    
    Args:
        notes: 笔记列表
        hours: 筛选最近 N 小时的笔记，None 表示不过滤
    
    Returns:
        筛选后的笔记列表
    """
    if hours is None:
        return notes
    
    now = datetime.now(timezone.utc)
    cutoff_time = now - timedelta(hours=hours)
    
    filtered = []
    for note in notes:
        created_at = note.get("created_at", "")
        if created_at:
            try:
                # 解析 ISO 格式时间
                note_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if note_time >= cutoff_time:
                    filtered.append(note)
            except:
                # 如果解析失败，保留笔记
                filtered.append(note)
        else:
            # 没有时间信息的也保留
            filtered.append(note)
    
    return filtered


def parse_note_content(note_detail):
    """
    解析笔记内容，提取文本
    
    Get 笔记的内容可能是多种格式：
    - 纯文本
    - 录音转文字
    - 图片 OCR 文字
    """
    content = ""
    
    # 尝试获取不同格式的内容
    if "content" in note_detail:
        content = note_detail["content"]
    elif "text" in note_detail:
        content = note_detail["text"]
    elif "transcript" in note_detail:
        # 录音转文字
        content = note_detail["transcript"]
    
    return content.strip()


def create_notion_page(note, note_detail):
    """
    在 Notion 碎片中心创建页面
    """
    title = note.get("title", "")
    note_id = str(note.get("id", ""))
    created_at = note.get("created_at", "")
    
    # 解析内容
    content = parse_note_content(note_detail) if note_detail else ""
    
    # 如果没有标题，使用内容前 50 字作为标题
    if not title and content:
        title = content[:50] + "..." if len(content) > 50 else content
    elif not title:
        title = f"Get笔记 {note_id[:8]}"
    
    # 提取标签（如果有）
    tags = []
    if "tags" in note and note["tags"]:
        tags = [t.get("name", "") for t in note["tags"] if t.get("name")]
    
    # 构建页面属性
    properties = {
        "Name": {
            "title": [{"text": {"content": title[:100]}}]
        },
        "创建时间": {
            "created_time": created_at if created_at else datetime.now(timezone.utc).isoformat()
        },
        "删除": {
            "checkbox": False
        }
    }
    
    # 添加标签（如果有 Tags 字段）
    if tags:
        properties["Tags"] = {
            "multi_select": [{"name": t} for t in tags[:10]]  # 最多 10 个标签
        }
    
    # 添加来源链接
    note_url = f"https://biji.com/note/{note_id}"
    properties["Link"] = {"url": note_url}
    
    # 构建页面内容（正文）
    children = []
    
    # 添加元数据段落
    meta_parts = [f"来源: Get笔记"]
    if created_at:
        meta_parts.append(f"创建时间: {created_at}")
    if tags:
        meta_parts.append(f"标签: {', '.join(tags)}")
    
    children.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": " | ".join(meta_parts)}}],
            "color": "gray"
        }
    })
    
    # 添加分隔线
    children.append({
        "object": "block",
        "type": "divider",
        "divider": {}
    })
    
    # 添加正文内容
    if content:
        # 如果内容很长，分段添加
        max_chunk = 2000  # Notion 块限制
        for i in range(0, len(content), max_chunk):
            chunk = content[i:i+max_chunk]
            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": chunk}}]
                }
            })
    else:
        children.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": "（无文本内容）"}}],
                "color": "gray"
            }
        })
    
    # 创建页面
    body = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": properties,
        "children": children[:100]  # Notion 限制最多 100 个块
    }
    
    result = notion_request("/pages", body)
    return result


def sync_note(note, processed_ids):
    """
    同步单条笔记到 Notion
    
    Returns:
        (success: bool, note_id: str)
    """
    note_id = str(note.get("id", ""))
    
    # 检查是否已处理
    if note_id in processed_ids:
        return True, note_id  # 视为成功，跳过
    
    # 获取笔记详情
    note_detail = fetch_note_detail(note_id)
    
    # 创建 Notion 页面
    result = create_notion_page(note, note_detail)
    
    if result and result.get("id"):
        print(f"   ✅ 同步成功: {note.get('title', '')[:30] or '(无标题)'}...")
        return True, note_id
    else:
        print(f"   ❌ 同步失败: {note.get('title', '')[:30] or '(无标题)'}...")
        return False, note_id


# ============ 主流程 ============

def main():
    print("=" * 60)
    print("🚀 Get 笔记 → Notion 碎片中心 同步开始")
    print("=" * 60)
    
    # 验证配置
    if not GETNOTE_API_KEY:
        print("[ERROR] GETNOTE_API_KEY 未配置")
        sys.exit(1)
    if not NOTION_TOKEN:
        print("[ERROR] NOTION_TOKEN 未配置")
        sys.exit(1)
    if not NOTION_DB_ID:
        print("[ERROR] NOTION_DB_ID 未配置")
        sys.exit(1)
    
    # 加载已处理的笔记
    processed_ids = load_processed_ids()
    print(f"[INFO] 已同步笔记数: {len(processed_ids)}")
    
    # 拉取 Get 笔记列表
    print(f"[INFO] 正在拉取 Get 笔记...")
    all_notes = fetch_getnote_notes()
    
    if not all_notes:
        print("[INFO] 没有获取到笔记，跳过")
        return
    
    # 按时间筛选
    recent_notes = filter_notes_by_time(all_notes, CHECK_HOURS)
    print(f"[INFO] 最近 {CHECK_HOURS} 小时内的新笔记: {len(recent_notes)} 条")
    
    # 过滤已处理的
    new_notes = [n for n in recent_notes if str(n.get("id", "")) not in processed_ids]
    print(f"[INFO] 需要同步的新笔记: {len(new_notes)} 条")
    
    if not new_notes:
        print("✨ 没有新的笔记需要同步")
        return
    
    # 同步每条笔记
    success_count = 0
    new_processed = set()
    
    for i, note in enumerate(new_notes, 1):
        note_title = note.get("title", "")[:30] or "(无标题)"
        print(f"\n[{i}/{len(new_notes)}] 正在同步: {note_title}...")
        
        success, note_id = sync_note(note, processed_ids)
        
        if success:
            success_count += 1
            new_processed.add(note_id)
        
        # 更新已处理记录（每 5 条保存一次，防止中断丢失进度）
        if i % 5 == 0:
            all_processed = processed_ids | new_processed
            save_processed_ids(all_processed)
    
    # 最终保存
    all_processed = processed_ids | new_processed
    save_processed_ids(all_processed)
    
    # 输出统计
    print("\n" + "=" * 60)
    print(f"🎉 同步完成!")
    print(f"   本次尝试: {len(new_notes)} 条")
    print(f"   同步成功: {success_count} 条")
    print(f"   同步失败: {len(new_notes) - success_count} 条")
    print(f"   累计已同步: {len(all_processed)} 条")
    print("=" * 60)
    
    # 如果有失败，返回非零退出码
    if success_count < len(new_notes):
        sys.exit(1)


if __name__ == "__main__":
    main()
