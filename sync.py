#!/usr/bin/env python3
"""
Get 笔记「每日日记」→ AI 分析 → Notion 日记中心 自动同步

流程：
1. 从 Get 笔记 API 拉取录音笔记
2. 获取语音转写原文 (audio.original)
3. 从原文中检测是否包含「每日总结」关键字
4. 调用 DeepSeek AI 分析原文，提取各维度
5. 将结果写入 Notion 日记中心对应字段
"""

import json
import os
import sys
import time
import socket
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

# ============ 配置 ============
GETNOTE_API_KEY = os.environ["GETNOTE_API_KEY"]
GETNOTE_CLIENT_ID = os.environ["GETNOTE_CLIENT_ID"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DB_ID = os.environ["NOTION_DB_ID"]
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]

TZ_CN = timezone(timedelta(hours=8))
socket.setdefaulttimeout(30)

# 情绪模板：名称 → Notion page_id（已有记录，不新建）
EMOTION_MAP = {
    "喜悦": "19533b33-7f23-80f4-9426-f84b367059c7",
    "平静": "19033b33-7f23-80c1-8c4e-f2b48215cadb",
    "悲伤": "2e433b33-7f23-80cb-8a4f-fee906021c68",
    "感动": "19133b33-7f23-80c6-b31f-c68d3e574612",
    "愤怒": "24f33b33-7f23-800e-b13d-f20fe4855636",
    "放松": "1a633b33-7f23-8089-b735-c60d7e5c4dc5",
    "沮丧": "33733b33-7f23-805e-b1b9-c53426d3be89",
    "混沌": "2dd33b33-7f23-8097-967f-ee4938c10185",
    "激动": "1a433b33-7f23-80ae-9da7-f948a8cdfecc",
    "烦躁": "19a33b33-7f23-807e-832b-c4c29c2dd93b",
    "焦虑": "2e633b33-7f23-80e8-ae53-c3b3d97d5875",
    "疲惫": "33733b33-7f23-803b-b163-c2ad7a420bf3",
    "痛苦": "19433b33-7f23-80d5-a97f-f88373cd9352",
    "紧张": "1a533b33-7f23-8002-ba12-f6e766ff74b2",
}

SCORE_OPTIONS = ["糟糕", "较差", "一般", "较好", "完美"]
HEALTH_OPTIONS = ["很好", "正常", "生病", "较差"]

HEALTH_DB_ID = "17a33b33-7f23-8090-abec-d9fbf88a829a"
EMOTION_DB_ID = "18933b33-7f23-80f0-9d83-ff497ac5a887"
SLEEP_DB_ID = "1ba33b33-7f23-8054-988c-c976153e354a"


# ============ 工具函数 ============
def api_call(url, method="GET", body=None, headers=None, retries=5):
    hdrs = headers or {}
    req = Request(url, method=method, data=json.dumps(body).encode() if body else None, headers=hdrs)
    for attempt in range(retries):
        try:
            resp = urlopen(req, timeout=30)
            return json.loads(resp.read())
        except Exception as e:
            err_str = str(e)
            retryable = any(kw in err_str for kw in ["429", "Connection reset", "timeout", "timed out", "104", "ConnectionRefused"])
            if attempt < retries - 1 and retryable:
                wait = 3 * (attempt + 1)
                print(f"[WARN] 请求失败 ({err_str[:80]})，{wait}s 后重试 ({attempt+1}/{retries})...", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"[ERROR] API call failed after {retries} retries: {e}", file=sys.stderr)
            raise


def getnote_get(path):
    return api_call(
        f"https://openapi.biji.com{path}",
        headers={"Authorization": GETNOTE_API_KEY, "X-Client-ID": GETNOTE_CLIENT_ID},
    )


def notion_post(path, body):
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


def deepseek_chat(system_prompt, user_message, model="deepseek-chat"):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.1,
        "max_tokens": 1000,
        "response_format": {"type": "json_object"},
    }
    result = api_call(
        "https://api.deepseek.com/v1/chat/completions",
        method="POST",
        body=body,
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    content = result["choices"][0]["message"]["content"]
    print(f"[INFO] DeepSeek 响应: {content[:300]}")
    return json.loads(content)


# ============ 时间逻辑 ============
def get_time_range():
    """返回 (target_date, start_time, end_time)
    
    始终检查今天的数据：
    - 今天 00:00 ~ now
    但在凌晨 0~4 点时，同时检查昨天的（覆盖深夜录音）
    """
    now = datetime.now(TZ_CN)
    today = now.strftime("%Y-%m-%d")
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now
    return today, start.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M")


def get_catchup_time_range():
    """凌晨 0~4 点时，额外检查昨天的数据"""
    now = datetime.now(TZ_CN)
    yesterday = now - timedelta(days=1)
    yesterday_date = yesterday.strftime("%Y-%m-%d")
    start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    end = yesterday.replace(hour=23, minute=59, second=59, microsecond=0) + timedelta(hours=4)
    return yesterday_date, start.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M")


# ============ Get 笔记 ============
def fetch_getnote_notes():
    print("[INFO] 拉取 Get 笔记列表...")
    all_notes = []
    since_id = 0
    while True:
        data = getnote_get(f"/open/api/v1/resource/note/list?since_id={since_id}")
        notes = data.get("data", {}).get("notes", [])
        if not notes:
            break
        all_notes.extend(notes)
        if not data.get("data", {}).get("has_more", False):
            break
        since_id = data.get("data", {}).get("next_cursor", 0)
    print(f"[INFO] 共拉取 {len(all_notes)} 条笔记")
    return all_notes


def is_recorder_type(note_type):
    return note_type in ("recorder_flash_audio", "recorder_audio", "audio", "meeting",
                         "internal_record", "local_audio", "class_audio")


def get_note_original_text(note):
    """获取笔记的原文（优先 audio.original，其次 content）"""
    note_id = note.get("note_id", "")
    note_type = note.get("note_type", "")

    if is_recorder_type(note_type):
        print(f'[INFO] 拉取录音详情: {note.get("title", "")}')
        try:
            detail = getnote_get(f"/open/api/v1/resource/note/detail?id={note_id}")
            detail_note = detail.get("data", {}).get("note", {})
            audio = detail_note.get("audio", {})
            original = audio.get("original", "") if isinstance(audio, dict) else ""
            content = detail_note.get("content", "")
            # 优先用原文（逐字稿），其次用 AI 整理后的内容
            return original if original else content
        except Exception as e:
            print(f"[WARN] 获取详情失败: {e}")
            return ""
    else:
        return note.get("content", "")


def find_daily_notes(notes, start_time, end_time):
    """从所有笔记中筛选：
    1. 录音类型 或 有内容的笔记
    2. 时间在范围内
    3. 原文中包含「每日总结」
    """
    candidates = []
    for note in notes:
        created_at = note.get("created_at", "")
        if created_at < start_time or created_at > end_time:
            continue

        # 先检查标签（快速过滤）
        tags = note.get("tags", [])
        tag_names = [t.get("name", "") for t in tags]
        has_tag = "每日总结" in tag_names

        # 获取原文
        original_text = get_note_original_text(note)
        if not original_text:
            continue

        # 检查原文中是否有「每日总结」
        has_keyword = "每日总结" in original_text

        if not has_tag and not has_keyword:
            continue

        candidates.append({
            "note_id": note.get("note_id", ""),
            "title": note.get("title", ""),
            "original_text": original_text.strip(),
            "type": note.get("note_type", ""),
            "created_at": created_at,
            "matched_by": "tag" if has_tag else "keyword",
        })

    # 按 created_at 排序
    candidates.sort(key=lambda x: x["created_at"])
    return candidates


# ============ AI 分析 ============
ANALYSIS_PROMPT = """你是一个日记分析助手。用户会给你一段口语化的录音转写原文，你需要从中提取多个维度的信息，以 JSON 格式返回。

用户会在录音开头说"每日总结"，之后的内容是自由表达的日记。请分析并提取以下字段（如果没提到，设为 null）：

1. "score": 今日整体评分。可选值：["糟糕", "较差", "一般", "较好", "完美"]。用户可能说"今天一般般"、"感觉不太好"、"完美的一天"等。

2. "health": 今日健康状况。可选值：["很好", "正常", "生病", "较差"]。如果用户提到身体不适、感冒、头疼、肚子疼等。

3. "emotion": 今日主要情绪。可选值：["喜悦", "平静", "悲伤", "感动", "愤怒", "放松", "沮丧", "混沌", "激动", "烦躁", "焦虑", "疲惫", "痛苦", "紧张"]。只选最匹配的一个。

4. "gratitude": 感恩日记。如果用户提到感恩、感谢、感激的人或事，整理成通顺文字（50-200字）。

5. "success": 成功日记。如果用户提到成就、进步、完成的事、做得好的事，整理成通顺文字（50-200字）。

6. "banana": 是否提到自慰相关内容。布尔值 true/false。

7. "summary": 今日总结。**重要**：排除以上所有已提取的维度内容后，将剩余内容整理成通顺的总结（100-300字）。
   - 如果用户说了"今天状态一般"并已被提取为 score=一般，那总结里不要重复说"状态一般"
   - 如果用户说了"今天感冒了"并已被提取为 health=生病，那总结里不要重复说生病
   - 总结只保留纯粹的日常事件、想法、经历

重要规则：
- 只返回 JSON，不要任何解释
- 没提到的维度设为 null，不要编造
- 保留用户原意，不要过度润色
- 中文输出"""


def analyze_with_ai(original_text):
    print(f"[INFO] 调用 DeepSeek 分析 ({len(original_text)} 字符)...")
    result = deepseek_chat(ANALYSIS_PROMPT, original_text)

    # 校验并规范化
    if result.get("score") and result["score"] not in SCORE_OPTIONS:
        result["score"] = None
    if result.get("health") and result["health"] not in HEALTH_OPTIONS:
        result["health"] = None
    if result.get("emotion") and result["emotion"] not in EMOTION_MAP:
        result["emotion"] = None

    print(f'[INFO] 分析结果: score={result.get("score")} health={result.get("health")} '
          f'emotion={result.get("emotion")} banana={result.get("banana")}')
    return result


# ============ Notion 写入 ============
def find_notion_page(target_date):
    print(f"[INFO] 查找 {target_date} 的日记...")
    data = notion_post(
        f"/databases/{NOTION_DB_ID}/query",
        {"filter": {"property": "日期", "date": {"equals": target_date}}, "page_size": 1},
    )
    results = data.get("results", [])
    if results:
        page_id = results[0]["id"]
        print(f"[INFO] 找到日记: {page_id}")
        return page_id
    else:
        print(f"[WARN] 未找到 {target_date} 的日记", file=sys.stderr)
        return None


def create_health_record(target_date, health_status):
    if not health_status:
        return None
    print(f"[INFO] 创建健康记录: {health_status}")
    result = notion_post(
        f"/databases/{HEALTH_DB_ID}/pages",
        {
            "parent": {"database_id": HEALTH_DB_ID},
            "properties": {
                "名称": {"title": [{"type": "text", "text": {"content": health_status}}]},
                "单选": {"select": {"name": health_status}},
                "日期": {"date": {"start": target_date}},
            },
        },
    )
    return result["id"]


def update_notion_page(page_id, analysis, target_date):
    update_props = {}

    # 评分
    if analysis.get("score"):
        update_props["评分"] = {"select": {"name": analysis["score"]}}

    # 感恩日记
    if analysis.get("gratitude"):
        text = analysis["gratitude"][:2000]
        update_props["💗感恩日记"] = {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        }

    # 成功日记
    if analysis.get("success"):
        text = analysis["success"][:2000]
        update_props["☀️成功日记"] = {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        }

    # 总结（已排除其他维度的内容）
    if analysis.get("summary"):
        text = analysis["summary"][:2000]
        update_props["总结"] = {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        }

    # 🍌
    if analysis.get("banana") is not None:
        update_props["🍌"] = {"checkbox": analysis["banana"]}

    # 情绪 - 关联已有记录
    if analysis.get("emotion"):
        emotion_name = analysis["emotion"]
        emotion_page_id = EMOTION_MAP.get(emotion_name)
        if emotion_page_id:
            try:
                notion_patch(f"/pages/{emotion_page_id}", {
                    "properties": {"创建日期": {"date": {"start": target_date}}}
                })
            except Exception as e:
                print(f"[WARN] 更新情绪日期失败: {e}")
            update_props["情绪"] = {"relation": [{"id": emotion_page_id}]}

    # 健康 - 创建记录并关联
    if analysis.get("health"):
        health_record_id = create_health_record(target_date, analysis["health"])
        if health_record_id:
            update_props["健康"] = {"relation": [{"id": health_record_id}]}

    if not update_props:
        print("[INFO] 没有需要更新的字段")
        return

    print(f"[INFO] 更新 Notion 页面，字段: {list(update_props.keys())}")
    notion_patch(f"/pages/{page_id}", {"properties": update_props})
    print(f"[INFO] ✅ Notion 更新完成")


# ============ 主流程 ============
def process_date(target_date, start_time, end_time, notes):
    """处理某个日期的录音"""
    daily_notes = find_daily_notes(notes, start_time, end_time)
    if not daily_notes:
        return False

    print(f"[INFO] 找到 {len(daily_notes)} 条「每日总结」录音")
    for dn in daily_notes:
        print(f'  - {dn["title"]} ({dn["created_at"]}) [匹配方式: {dn["matched_by"]}]')

    # 合并原文
    all_original = []
    for i, note in enumerate(daily_notes):
        if note["original_text"]:
            all_original.append(f"--- 录音 {i+1} ({note['created_at']}) ---\n{note['original_text']}")

    if not all_original:
        print("[WARN] 所有录音原文为空，跳过。")
        return False

    combined_text = "\n\n".join(all_original)
    print(f"[INFO] 合并原文共 {len(combined_text)} 字符")

    # AI 分析
    analysis = analyze_with_ai(combined_text)

    # 找到 Notion 页面
    page_id = find_notion_page(target_date)
    if not page_id:
        print(f"[ERROR] 找不到 {target_date} 的日记页面", file=sys.stderr)
        return False

    # 写入 Notion
    update_notion_page(page_id, analysis, target_date)
    return True


def main():
    now = datetime.now(TZ_CN)
    hour = now.hour
    print(f"[INFO] === 当前时间: {now.strftime('%Y-%m-%d %H:%M')} (UTC+8) ===")

    # 拉取所有笔记（只拉一次）
    notes = fetch_getnote_notes()

    processed = False

    # 1. 始终检查今天的数据
    today_date, start, end = get_time_range()
    print(f"[INFO] 检查今天 ({today_date}): {start} ~ {end}")
    if process_date(today_date, start, end, notes):
        processed = True

    # 2. 凌晨 0~4 点额外检查昨天（覆盖深夜录音）
    if hour < 4:
        catchup_date, catchup_start, catchup_end = get_catchup_time_range()
        print(f"[INFO] 兜底检查昨天 ({catchup_date}): {catchup_start} ~ {catchup_end}")
        if process_date(catchup_date, catchup_start, catchup_end, notes):
            processed = True

    if not processed:
        print("[INFO] 没有需要处理的录音，跳过。")
    else:
        print("[INFO] ✅ 全部完成！")


if __name__ == "__main__":
    main()
