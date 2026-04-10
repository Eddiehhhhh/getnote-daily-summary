#!/usr/bin/env python3
"""
Get 笔记「每日总结」→ Notion 日记中心 自动同步

两种模式：
- 实时模式（每 5 分钟触发）：检查今天 00:00 ~ now 的笔记，找到新的「每日总结」立刻同步
- 兜底模式（凌晨 2 点触发）：检查昨天的笔记，确保不漏
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
def get_mode():
    """根据当前时间决定运行模式。
    
    实时模式：04:00 ~ 次日 04:00 (UTC+8)，检查今天 + 凌晨的笔记
    兜底模式：基本不触发，但如果有则检查前一天的
    """
    hour = datetime.now(TZ_CN).hour
    if hour >= 4:
        return "realtime"
    else:
        return "catchup"


def get_time_range(mode):
    """根据模式返回 (target_date, start_time, end_time)"""
    now = datetime.now(TZ_CN)

    if mode == "realtime":
        # 实时模式：目标日期 = 今天，时间范围 = 今天 00:00 ~ now
        target_date = now.strftime("%Y-%m-%d")
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
        return target_date, start.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M")
    else:
        # 兜底模式：目标日期 = 昨天，时间范围 = 昨天 00:00 ~ 今天 04:00
        yesterday = now - timedelta(days=1)
        target_date = yesterday.strftime("%Y-%m-%d")
        start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        end = yesterday.replace(hour=23, minute=59, second=59, microsecond=0) + timedelta(hours=4)
        return target_date, start.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M")


def fetch_getnote_notes():
    """拉取所有笔记"""
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


def find_daily_summary(notes, start_time, end_time):
    """从笔记中筛选 tags 包含「每日总结」的，且在时间范围内"""
    candidates = []
    for note in notes:
        created_at = note.get("created_at", "")

        # 检查时间范围
        if created_at < start_time or created_at > end_time:
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


def find_notion_page(target_date):
    """在 Notion 日记中心找到对应日期的记录"""
    print(f"[INFO] 在 Notion 日记中心查找 {target_date} 的记录...")
    data = notion_post(
        f"/databases/{NOTION_DB_ID}/query",
        {
            "filter": {
                "property": "日期",
                "date": {"equals": target_date},
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
        print(f"[WARN] 未找到 {target_date} 的日记记录", file=sys.stderr)
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
    mode = get_mode()
    target_date, start_time, end_time = get_time_range(mode)

    mode_label = "实时" if mode == "realtime" else "兜底"
    print(f"[INFO] === {mode_label}模式：同步 {target_date} 的每日总结 ===")
    print(f"[INFO] 笔记时间范围: {start_time} ~ {end_time}")

    # 1. 拉取 Get 笔记
    notes = fetch_getnote_notes()

    # 2. 筛选每日总结
    summaries = find_daily_summary(notes, start_time, end_time)

    if not summaries:
        print(f"[INFO] {target_date} 没有找到新的「每日总结」笔记，跳过。")
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
    page_id = find_notion_page(target_date)
    if not page_id:
        print(f"[ERROR] 无法找到 {target_date} 的日记页面", file=sys.stderr)
        sys.exit(1)

    # 4. 写入 Notion
    update_notion_summary(page_id, final_content)

    print(f"[INFO] ✅ 同步完成！{target_date} 的每日总结已写入 Notion。")


if __name__ == "__main__":
    main()
