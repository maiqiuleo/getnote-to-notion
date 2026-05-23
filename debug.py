#!/usr/bin/env python3
"""
Get 笔记 API 调试脚本
用于查看 API 实际返回的 JSON 结构，帮助确认字段名称
运行方式：python debug.py
"""

import json
import os
import ssl
from urllib.error import HTTPError
from urllib.request import Request, urlopen

GETNOTE_API_KEY = os.environ.get("GETNOTE_API_KEY", "")
GETNOTE_CLIENT_ID = os.environ.get("GETNOTE_CLIENT_ID", "")


def getnote_request(path):
    url = f"https://openapi.biji.com{path}"
    req = Request(url, method="GET")
    req.add_header("Authorization", GETNOTE_API_KEY)
    req.add_header("X-Client-ID", GETNOTE_CLIENT_ID)
    ctx = ssl.create_default_context()
    try:
        with urlopen(req, context=ctx, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        print(f"[ERROR] {e.code}: {e.read().decode()[:500]}")
        return None
    except Exception as e:
        print(f"[ERROR] {e}")
        return None


def main():
    print("=" * 60)
    print("📋 Get 笔记 API 结构调试")
    print("=" * 60)

    # ---- 1. 笔记列表 ----
    print("\n【1】笔记列表接口 (since_id=0, 取第一条)")
    result = getnote_request("/open/api/v1/resource/note/list?since_id=0")
    if not result:
        print("[ERROR] 列表接口请求失败")
        return

    print(f"success: {result.get('success')}")
    notes = result.get("data", {}).get("notes", [])
    print(f"返回笔记数: {len(notes)}")

    if not notes:
        print("[WARN] 没有笔记，无法继续")
        return

    first_note = notes[0]
    print("\n--- 列表接口：第一条笔记完整字段 ---")
    print(json.dumps(first_note, ensure_ascii=False, indent=2))

    # ---- 2. 笔记详情 ----
    note_id = str(first_note.get("id") or first_note.get("note_id") or "")
    if not note_id:
        print("[WARN] 无法获取笔记 ID")
        return

    print(f"\n【2】笔记详情接口 (note_id={note_id})")
    detail_result = getnote_request(f"/open/api/v1/resource/note/detail?id={note_id}")
    if not detail_result:
        print("[ERROR] 详情接口请求失败")
        return

    print(f"success: {detail_result.get('success')}")
    detail_data = detail_result.get("data", {})
    note_detail = detail_data.get("note") if isinstance(detail_data, dict) else detail_data

    print("\n--- 详情接口：完整 data 字段 ---")
    print(json.dumps(detail_data, ensure_ascii=False, indent=2))

    # ---- 3. 关键字段摘要 ----
    print("\n【3】关键字段检查")
    fields_to_check = [
        "id", "title", "content", "text", "body",
        "tags", "tag_list",
        "topics", "topic_list", "topic_name",
        "databases", "database_list", "database_name",
        "notebooks", "notebook_list", "notebook_name",
        "folders", "folder_list", "folder_name",
        "collections", "spaces",
        "created_at", "updated_at", "createdAt", "updatedAt",
        "ai_note", "ai_summary", "summary", "excerpt",
        "transcript", "ocr_text",
        "source_url", "url", "link",
    ]

    sources = {"list": first_note, "detail": note_detail or detail_data}
    for source_name, source in sources.items():
        if not isinstance(source, dict):
            continue
        print(f"\n  来源: {source_name}")
        for field in fields_to_check:
            val = source.get(field)
            if val is not None:
                val_preview = str(val)[:80].replace('\n', ' ')
                print(f"    ✅ {field}: {val_preview}")
            else:
                print(f"    ❌ {field}: 不存在")

    print("\n" + "=" * 60)
    print("✅ 调试完成，请将以上输出提供给开发者")
    print("=" * 60)


if __name__ == "__main__":
    main()
