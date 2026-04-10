#!/usr/bin/env python3
"""
Get 笔记「每日总结」→ Notion 日记中心 自动同步

流程：
1. 从 Get 笔记 API 拉取前一天的笔记列表
2. 筛选 tags 包含「每日总结」的笔记
3. 在 Notion 日记中心找到前一天的记录
4. 将「每日总结」内容写入「总结」字段
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

# ============ 配置 ============
GETNOTE_API_KEY = os.environ["GETNOTE_API_KEY"]
GETNOTE_CLIENT_ID = os.environ["GETNOTE_CLIENT_ID"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DB_ID = os.environ["NOTION_DB_ID"]  # 日记中心数据库 ID

TZ_CN = timezone(timedelta(hours=8))


# ============ 工具函数 ============
def api_call(url, method="GET", body=None, headers=None, retries=3):
    """通用 API 请求（带重试）"""
    import time
    hdrs = headers or {}
    req = Request(url, method=method, data=json.dumps(body).encode() if body else None, headers=hdrs)
    for attempt in range(retries):
        try:
            resp = urlopen(req)
            return json.loads(resp.read())
        except Exception as e:
            if attempt < retries - 1 and "429" in str(e):
                wait = 3 * (attempt + 1)
                print(f"[WARN] 限流，等待 {wait}s 后重试...", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"[ERROR] API call failed: {e}", file=sys.stderr)
            sys.exit(1)


def getnote_get(path):
    """Get 笔记 GET 请求"""
    return api_call(
        f"https://openapi.biji.com{path}",
        headers={
            "Authorization": GETNOTE_API_KEY,
            "X-Client-ID": GETNOTE_CLIENT_ID,
        },
    )


def notion_post(path, body):
    """Notion POST 请求"""
    return api_call(
        f"https://api.notion.com/v1{path}",
        method="POST",
        body=body,
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
    )


def notion_patch(path, body):
    """Notion PATCH 请求"""
    return api_call(
        f"https://api.notion.com/v1{path}",
        method="PATCH",
        body=body,
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
    )


# ============ 主逻辑 ============
def get_yesterday():
    """获取昨天的日期字符串 (YYYY-MM-DD)"""
    yesterday = datetime.now(TZ_CN) - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")


def fetch_getnote_notes():
    """拉取所有笔记，筛选昨天的"""
    print("[INFO] 拉取 Get 笔记列表...")
    all_notes = []
    since_id = 0

    while True:
        data = getnote_get(f"/open/api/v1/resource/note/list?since_id={since_id}")
        notes = data.get("data", {}).get("notes", [])
        if not notes:
            break
        all_notes.extend(notes)
        has_more = data.get("data", {}).get("has_more", False)
        if not has_more:
            break
        since_id = data.get("data", {}).get("next_cursor", 0)

    print(f"[INFO] 共拉取 {len(all_notes)} 条笔记")
    return all_notes


def find_daily_summary(notes, target_date):
    """从笔记中筛选 tags 包含「每日总结」的"""
    candidates = []
    for note in notes:
        created_at = note.get("created_at", "")
        # 只看目标日期的笔记
        if not created_at.startswith(target_date):
            continue

        tags = note.get("tags", [])
        tag_names = [t.get("name", "") for t in tags]

        # 检查 tags 是否包含「每日总结」
        if "每日总结" not in tag_names:
            continue

        note_id = note.get("note_id", "")
        note_type = note.get("note_type", "")
        title = note.get("title", "")
        content = note.get("content", "")

        # 对于录音类型，拉取详情获取完整转写
        if note_type in ("recorder_flash_audio", "recorder_audio", "audio", "meeting", "internal_record", "local_audio", "class_audio"):
            print(f"[INFO] 拉取录音笔记详情: {title}")
            detail = get_note_detail(note_id)
            detail_note = detail.get("data", {}).get("note", {})
            full_content = detail_note.get("content", "")
            if full_content:
                content = full_content

        candidates.append({
            "note_id": note_id,
            "title": title,
            "content": content.strip(),
            "type": note_type,
            "created_at": created_at,
        })

    return candidates


def get_note_detail(note_id):
    """获取笔记详情（录音类型可能有更完整的转写内容）"""
    return getnote_get(f"/open/api/v1/resource/note/detail?id={note_id}")


def find_notion_page(yesterday_date):
    """在 Notion 日记中心找到昨天的记录"""
    print(f"[INFO] 在 Notion 日记中心查找 {yesterday_date} 的记录...")
    data = notion_post(
        f"/databases/{NOTION_DB_ID}/query",
        {
            "filter": {
                "property": "日期",
                "date": {"equals": yesterday_date},
            },
            "page_size": 1,
        },
    )

    results = data.get("results", [])
    if results:
        page = results[0]
        title_parts = page.get("properties", {}).get("名称", {}).get("title", [])
        title = "".join([t.get("plain_text", "") for t in title_parts])
        print(f"[INFO] 找到记录: {title} (id: {page['id']})")
        return page["id"]
    else:
        print(f"[WARN] 未找到 {yesterday_date} 的日记记录", file=sys.stderr)
        return None


def update_notion_summary(page_id, content):
    """更新 Notion 页面的「总结」字段"""
    # Notion rich_text 有 2000 字符限制
    max_len = 2000
    truncated = content[:max_len] if len(content) > max_len else content

    print(f"[INFO] 写入总结到 Notion ({len(truncated)} 字符)...")
    result = notion_patch(
        f"/pages/{page_id}",
        {
            "properties": {
                "总结": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": truncated},
                        }
                    ]
                }
            }
        },
    )
    return result


def main():
    yesterday = get_yesterday()
    print(f"[INFO] === 开始同步 {yesterday} 的每日总结 ===")

    # 1. 拉取 Get 笔记
    notes = fetch_getnote_notes()

    # 2. 筛选每日总结
    summaries = find_daily_summary(notes, yesterday)

    if not summaries:
        print(f"[INFO] {yesterday} 没有找到「每日总结」笔记，跳过。")
        return

    print(f"[INFO] 找到 {len(summaries)} 条每日总结")

    # 取第一条（如果有多条，合并）
    if len(summaries) == 1:
        final_content = summaries[0]["content"]
    else:
        # 多条则合并
        parts = []
        for i, s in enumerate(summaries):
            prefix = f"--- 记录 {i+1} ({s['created_at']}) ---\n"
            parts.append(prefix + s["content"])
        final_content = "\n\n".join(parts)

    if not final_content.strip():
        print(f"[WARN] 每日总结内容为空，跳过。", file=sys.stderr)
        return

    print(f"[INFO] 总结内容预览: {final_content[:100]}...")

    # 3. 找到 Notion 日记页面
    page_id = find_notion_page(yesterday)
    if not page_id:
        print(f"[ERROR] 无法找到 {yesterday} 的日记页面", file=sys.stderr)
        sys.exit(1)

    # 4. 写入 Notion
    update_notion_summary(page_id, final_content)

    print(f"[INFO] ✅ 同步完成！{yesterday} 的每日总结已写入 Notion。")


if __name__ == "__main__":
    main()
