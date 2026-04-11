#!/usr/bin/env python3
"""
Get 笔记「每日日记」→ AI 分析 → Notion 日记中心 自动同步

流程：
1. 从 Get 笔记 API 拉取录音笔记
2. 获取语音转写原文 (audio.original)
3. 调用 DeepSeek AI 分析原文，提取各维度
4. 将结果写入 Notion 日记中心对应字段
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

# ============ 配置 ============
GETNOTE_API_KEY = os.environ["GETNOTE_API_KEY"]
GETNOTE_CLIENT_ID = os.environ["GETNOTE_CLIENT_ID"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DB_ID = os.environ["NOTION_DB_ID"]  # 日记中心数据库 ID
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]

TZ_CN = timezone(timedelta(hours=8))

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

# 评分选项
SCORE_OPTIONS = ["糟糕", "较差", "一般", "较好", "完美"]

# 健康选项
HEALTH_OPTIONS = ["很好", "正常", "生病", "较差"]

# Notion 数据库 ID
HEALTH_DB_ID = "17a33b33-7f23-8090-abec-d9fbf88a829a"
EMOTION_DB_ID = "18933b33-7f23-80f0-9d83-ff497ac5a887"
SLEEP_DB_ID = "1ba33b33-7f23-8054-988c-c976153e354a"


# ============ 工具函数 ============
def api_call(url, method="GET", body=None, headers=None, retries=3):
    """通用 API 请求（带重试）"""
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
    """调用 DeepSeek API"""
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
    print(f"[INFO] DeepSeek 响应: {content[:200]}")
    return json.loads(content)


# ============ 时间逻辑 ============
def get_mode():
    """判断运行模式：04:00~次日04:00 = realtime, 00:00~04:00 = catchup"""
    hour = datetime.now(TZ_CN).hour
    return "catchup" if hour < 4 else "realtime"


def get_time_range(mode):
    """返回 (target_date, start_time, end_time)"""
    now = datetime.now(TZ_CN)
    if mode == "realtime":
        target_date = now.strftime("%Y-%m-%d")
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
        return target_date, start.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M")
    else:
        yesterday = now - timedelta(days=1)
        target_date = yesterday.strftime("%Y-%m-%d")
        start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        end = yesterday.replace(hour=23, minute=59, second=59, microsecond=0) + timedelta(hours=4)
        return target_date, start.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M")


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


def find_daily_notes(notes, start_time, end_time):
    """筛选带「每日总结」标签的录音笔记"""
    candidates = []
    for note in notes:
        created_at = note.get("created_at", "")
        if created_at < start_time or created_at > end_time:
            continue
        tags = note.get("tags", [])
        tag_names = [t.get("name", "") for t in tags]
        if "每日总结" not in tag_names:
            continue

        note_id = note.get("note_id", "")
        note_type = note.get("note_type", "")
        title = note.get("title", "")

        # 获取详情，拿原文
        if note_type in ("recorder_flash_audio", "recorder_audio", "audio", "meeting",
                         "internal_record", "local_audio", "class_audio"):
            print(f"[INFO] 拉取录音详情: {title}")
            detail = getnote_get(f"/open/api/v1/resource/note/detail?id={note_id}")
            detail_note = detail.get("data", {}).get("note", {})
            audio = detail_note.get("audio", {})
            original_text = audio.get("original", "") if isinstance(audio, dict) else ""
            content = detail_note.get("content", "")
            if not original_text and content:
                original_text = content
        else:
            original_text = note.get("content", "")

        candidates.append({
            "note_id": note_id,
            "title": title,
            "original_text": original_text.strip(),
            "content": note.get("content", "").strip(),
            "type": note_type,
            "created_at": created_at,
        })
    return candidates


# ============ AI 分析 ============
ANALYSIS_PROMPT = """你是一个日记分析助手。用户会给你一段口语化的录音转写原文，你需要从中提取以下维度信息，以 JSON 格式返回。

请提取以下字段（如果用户没提到某个维度，该字段设为 null）：

1. "score": 今日整体评分。可选值：["糟糕", "较差", "一般", "较好", "完美"]。用户可能用数字(1-5)或描述性语言表达。

2. "health": 今日健康状况。可选值：["很好", "正常", "生病", "较差"]。如果用户提到身体不适、生病、头疼、感冒等，选"生病"。

3. "emotion": 今日主要情绪。可选值：["喜悦", "平静", "悲伤", "感动", "愤怒", "放松", "沮丧", "混沌", "激动", "烦躁", "焦虑", "疲惫", "痛苦", "紧张"]。只选一个最匹配的。

4. "gratitude": 感恩日记内容。如果用户提到感恩、感谢、感激的人或事，提取相关内容，整理成一段通顺的文字（50-200字）。

5. "success": 成功日记内容。如果用户提到今天的成就、进步、完成的事、做得好的事，提取相关内容，整理成一段通顺的文字（50-200字）。

6. "banana": 是否提到自慰相关内容。布尔值 true/false。

7. "summary": 今日总结。排除以上所有维度后，用户提到的其他日常事项、想法、经历，整理成一段通顺的总结（100-300字）。如果用户在最后单独说了总结性内容，优先使用。

重要规则：
- 只返回 JSON，不要任何解释
- 如果用户没提到某个维度，设为 null，不要编造
- 内容要保留用户的原意，不要过度润色
- 中文输出"""

def analyze_with_ai(original_text):
    """调用 DeepSeek 分析录音原文"""
    print(f"[INFO] 调用 DeepSeek 分析 ({len(original_text)} 字符)...")
    result = deepseek_chat(ANALYSIS_PROMPT, original_text)

    # 校验并规范化字段
    if result.get("score") and result["score"] not in SCORE_OPTIONS:
        result["score"] = None
    if result.get("health") and result["health"] not in HEALTH_OPTIONS:
        result["health"] = None
    if result.get("emotion") and result["emotion"] not in EMOTION_MAP:
        result["emotion"] = None

    print(f"[INFO] 分析结果: score={result.get('score')} health={result.get('health')} "
          f"emotion={result.get('emotion')} banana={result.get('banana')}")
    return result


# ============ Notion 写入 ============
def find_notion_page(target_date):
    """在日记中心找到对应日期的页面"""
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
    """在健康数据库创建记录"""
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
    """更新 Notion 日记页面的多个字段"""
    update_props = {}

    # 1. 评分
    if analysis.get("score"):
        update_props["评分"] = {"select": {"name": analysis["score"]}}

    # 2. 感恩日记
    if analysis.get("gratitude"):
        text = analysis["gratitude"][:2000]
        update_props["💗感恩日记"] = {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        }

    # 3. 成功日记
    if analysis.get("success"):
        text = analysis["success"][:2000]
        update_props["☀️成功日记"] = {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        }

    # 4. 总结
    if analysis.get("summary"):
        text = analysis["summary"][:2000]
        update_props["总结"] = {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        }

    # 5. 香蕉 checkbox
    if analysis.get("banana") is not None:
        update_props["🍌"] = {"checkbox": analysis["banana"]}

    # 6. 情绪 - 关联已有的情绪记录
    if analysis.get("emotion"):
        emotion_name = analysis["emotion"]
        emotion_page_id = EMOTION_MAP.get(emotion_name)
        if emotion_page_id:
            # 先给情绪记录设置今天日期（复用已有记录）
            try:
                notion_patch(f"/pages/{emotion_page_id}", {
                    "properties": {
                        "创建日期": {"date": {"start": target_date}},
                    }
                })
            except Exception as e:
                print(f"[WARN] 更新情绪日期失败: {e}")
            update_props["情绪"] = {
                "relation": [{"id": emotion_page_id}]
            }

    # 7. 健康 - 在健康数据库创建记录并关联
    if analysis.get("health"):
        health_record_id = create_health_record(target_date, analysis["health"])
        if health_record_id:
            update_props["健康"] = {
                "relation": [{"id": health_record_id}]
            }

    if not update_props:
        print("[INFO] 没有需要更新的字段")
        return

    print(f"[INFO] 更新 Notion 页面，字段: {list(update_props.keys())}")
    notion_patch(f"/pages/{page_id}", {"properties": update_props})
    print(f"[INFO] ✅ Notion 更新完成")


# ============ 主流程 ============
def main():
    mode = get_mode()
    target_date, start_time, end_time = get_time_range(mode)
    mode_label = "实时" if mode == "realtime" else "兜底"
    print(f"[INFO] === {mode_label}模式：{target_date} ({start_time} ~ {end_time}) ===")

    # 1. 拉取笔记
    notes = fetch_getnote_notes()

    # 2. 筛选每日总结录音
    daily_notes = find_daily_notes(notes, start_time, end_time)
    if not daily_notes:
        print(f"[INFO] 没有新的「每日总结」录音，跳过。")
        return

    print(f"[INFO] 找到 {len(daily_notes)} 条录音笔记")

    # 3. 合并多条录音的原文
    all_original = []
    for i, note in enumerate(daily_notes):
        if note["original_text"]:
            all_original.append(f"--- 录音 {i+1} ({note['created_at']}) ---\n{note['original_text']}")

    if not all_original:
        print("[WARN] 所有录音的原文为空，跳过。", file=sys.stderr)
        return

    combined_text = "\n\n".join(all_original)
    print(f"[INFO] 合并原文共 {len(combined_text)} 字符")

    # 4. AI 分析
    analysis = analyze_with_ai(combined_text)

    # 5. 找到 Notion 日记页面
    page_id = find_notion_page(target_date)
    if not page_id:
        print(f"[ERROR] 找不到 {target_date} 的日记页面", file=sys.stderr)
        sys.exit(1)

    # 6. 写入 Notion
    update_notion_page(page_id, analysis, target_date)

    print(f"[INFO] ✅ 全部完成！{target_date} 日记已更新。")


if __name__ == "__main__":
    main()
