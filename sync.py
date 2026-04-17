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
SUCCESS_DB_ID = "17833b33-7f23-80f8-9e7b-f92f036f044c"
GRATITUDE_DB_ID = "17833b33-7f23-8086-8925-d1d4528f9098"
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
            # 尝试读取错误响应体（Notion 400 会包含详细错误信息）
            response_body = ""
            if hasattr(e, 'read'):
                try:
                    response_body = e.read().decode('utf-8', errors='replace')[:500]
                except:
                    pass
            retryable = any(kw in err_str for kw in ["429", "Connection reset", "timeout", "timed out", "104", "ConnectionRefused"])
            if attempt < retries - 1 and retryable:
                wait = 3 * (attempt + 1)
                print(f"[WARN] 请求失败 ({err_str[:80]})，{wait}s 后重试 ({attempt+1}/{retries})...", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"[ERROR] API call failed after {retries} retries: {e}", file=sys.stderr)
            if response_body:
                print(f"[ERROR] Response body: {response_body}", file=sys.stderr)
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


def notion_get(path):
    return api_call(
        f"https://api.notion.com/v1{path}",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
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
def get_target_time_range():
    """获取目标日期和时间范围

    核心规则：凌晨的录音（00:00~04:59）属于前一天的日记。
    用户通常在晚上或深夜录「每日总结」，回顾当天发生的事。
    例如：4/9 凌晨 1:00 录的「每日总结」讲的是 4/8 的事，应写入 4/8 日记。

    每天凌晨 5:00 运行时，检查范围是：昨天 00:00 ~ 今天 04:59
    全部归到昨天（前一天）的日记。

    Returns:
        (target_date, start_time, end_time)
        - target_date: 要写入的日记日期（前一天）
        - start_time: 笔记筛选起始时间（前一天 00:00）
        - end_time: 笔记筛选截止时间（今天 04:59）
    """
    now = datetime.now(TZ_CN)
    yesterday = now - timedelta(days=1)
    target_date = yesterday.strftime("%Y-%m-%d")

    # 起始：昨天 00:00
    start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    # 截止：今天 04:59（覆盖到凌晨 5 点前）
    cutoff_today = now.replace(hour=4, minute=59, second=59, microsecond=0)

    return (
        target_date,
        start.strftime("%Y-%m-%d %H:%M"),
        cutoff_today.strftime("%Y-%m-%d %H:%M"),
    )


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


DAILY_KEYWORDS = ["每日总结", "日常总结", "今日总结", "每日记录", "今日情绪"]


def find_daily_notes(notes, start_time, end_time):
    """从所有笔记中筛选：
    1. 录音类型 或 有内容的笔记
    2. 时间在范围内
    3. 标签包含「每日总结」或 标题/原文包含日记总结关键字
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

        # 检查标题（无需获取详情）
        note_title = note.get("title", "")
        has_title_keyword = any(kw in note_title for kw in DAILY_KEYWORDS)

        # 获取原文（只在标题匹配或标签匹配时才获取，节省 API 调用）
        original_text = ""
        if has_tag or has_title_keyword:
            original_text = get_note_original_text(note)

        if not original_text:
            continue

        # 检查原文中是否有关键字
        has_text_keyword = any(kw in original_text for kw in DAILY_KEYWORDS)

        if not has_tag and not has_title_keyword and not has_text_keyword:
            continue

        matched_by = "tag" if has_tag else ("title" if has_title_keyword else "keyword")
        candidates.append({
            "note_id": note.get("note_id", ""),
            "title": note.get("title", ""),
            "original_text": original_text.strip(),
            "type": note.get("note_type", ""),
            "created_at": created_at,
            "matched_by": matched_by,
        })

    # 按 created_at 排序
    candidates.sort(key=lambda x: x["created_at"])
    return candidates


# ============ AI 分析 ============
SLEEP_QUALITY_OPTIONS = ["优秀", "良好", "一般", "差"]
ENERGY_LEVEL_OPTIONS = ["充沛", "一般", "疲惫"]

ANALYSIS_PROMPT = """你是一个日记分析助手。用户会给你一段口语化的录音转写原文，你需要从中提取多个维度的信息，以 JSON 格式返回。

请分析并提取以下字段（如果没提到，设为 null）：

1. "score": 今日整体评分。可选值：["糟糕", "较差", "一般", "较好", "完美"]。

2. "health": 今日健康状况。可选值：["很好", "正常", "生病", "较差"]。如果用户提到身体不适、感冒、头疼、肚子疼、健康相关内容。

3. "emotion": 今日情绪（数组，可多个）。可选值：["喜悦", "平静", "悲伤", "感动", "愤怒", "放松", "沮丧", "混沌", "激动", "烦躁", "焦虑", "疲惫", "痛苦", "紧张"]。用户可能同时表达多种情绪（如"平静为主但也有疲惫"），全部提取出来放在数组中。没提到情绪则为 null。

4. "sleep": 睡眠相关信息（对象，如果没提到任何睡眠内容则为 null）。包含子字段：
   - "quality": 睡眠质量。可选值：["优秀", "良好", "一般", "差"]。
   - "energy": 醒来后能量水平。可选值：["充沛", "一般", "疲惫"]。
   - "dreams": 梦境记录。如果用户提到做梦的内容，整理成文字（50-200字）。没提到则为 null。

5. "gratitude": 感恩日记。如果用户提到感恩、感谢、感激的人或事，整理成通顺文字（50-200字）。

6. "success": 成功日记。如果用户提到成就、进步、完成的事、做得好的事，整理成通顺文字（50-200字）。

7. "banana": 是否提到自慰相关内容。布尔值 true/false。

8. "summary": 今日总结。**最关键的规则：总结是"剔除了结构化字段之后的剩余内容"。**

   写总结时，你必须先在心里完成这个排除：
   - score → 剔除：评分相关（"今天一般般"、"感觉不好"、"状态可以"等评价性描述）
   - health → 剔除：健康相关（"身体还好"、"有点感冒"、"嗓子疼"等身体状况描述）
   - emotion → 剔除：所有情绪描述（"心情平静"、"有点烦躁"、"挺开心的"、"感觉疲惫"等情绪表达）
   - sleep → 剔除：睡眠相关（几点睡、几点起、睡得好不好、做了什么梦、醒了状态如何等）
   - gratitude → 剔除：感恩感谢相关
   - success → 剔除：成就进步相关
   - banana → 剔除：放纵相关

   总结只保留：**纯粹的日常事件、具体活动、社交互动、见闻、想法思考**。
   如果剔除结构化字段后没什么内容了，总结可以简短（甚至50字），或者设为 null。宁可简短也不要重复。

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
    # emotion: 支持数组（多情绪）和字符串（单情绪）
    if result.get("emotion"):
        raw_emotion = result["emotion"]
        if isinstance(raw_emotion, str):
            raw_emotion = [raw_emotion]
        elif isinstance(raw_emotion, list):
            pass
        else:
            raw_emotion = None
        if raw_emotion:
            # 过滤无效值
            result["emotion"] = [e for e in raw_emotion if e in EMOTION_MAP]
            if not result["emotion"]:
                result["emotion"] = None
        else:
            result["emotion"] = None
    else:
        result["emotion"] = None

    # 校验 sleep 子字段
    sleep = result.get("sleep")
    if sleep and isinstance(sleep, dict):
        if sleep.get("quality") and sleep["quality"] not in SLEEP_QUALITY_OPTIONS:
            sleep["quality"] = None
        if sleep.get("energy") and sleep["energy"] not in ENERGY_LEVEL_OPTIONS:
            sleep["energy"] = None
        # 如果所有子字段都为空，整个 sleep 设为 null
        if not any(sleep.get(k) for k in ("quality", "energy", "dreams")):
            result["sleep"] = None
    else:
        result["sleep"] = None

    sleep_info = result.get("sleep", {})
    emotions = result.get("emotion", [])
    emotion_str = ",".join(emotions) if isinstance(emotions, list) and emotions else str(emotions)
    print(f'[INFO] 分析结果: score={result.get("score")} health={result.get("health")} '
          f'emotion=[{emotion_str}] banana={result.get("banana")} '
          f'sleep_quality={sleep_info.get("quality")} sleep_energy={sleep_info.get("energy")}')
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


def update_existing_record(record_id, props):
    """更新一条已有 Notion 页面的属性"""
    notion_patch(f"/pages/{record_id}", {"properties": props})


def update_title_record(record_id, db_name, title_field, new_title):
    """更新已有关联记录的标题"""
    if not record_id or not new_title:
        return
    print(f"[INFO] 更新已有{db_name}标题: {new_title[:30]}... (id={record_id[-8:]})")
    try:
        update_existing_record(record_id, {
            title_field: {"title": [{"type": "text", "text": {"content": new_title[:100]}}]}
        })
    except Exception as e:
        print(f"[WARN] 更新{db_name}失败: {e}")


def get_page_relations(page_id):
    """获取日记页面中各字段关联的记录 ID，只调用一次 Notion API"""
    page = notion_get(f"/pages/{page_id}")
    props = page["properties"]
    return {
        "sleep": props.get("睡眠记录", {}).get("relation", []),
        "health": props.get("健康", {}).get("relation", []),
        "success": props.get("成功日记", {}).get("relation", []),
        "gratitude": props.get("感恩日记", {}).get("relation", []),
    }


def update_notion_page(page_id, analysis, target_date):
    update_props = {}

    # 一次性获取所有关联记录
    rels = get_page_relations(page_id)

    # 评分
    if analysis.get("score"):
        update_props["评分"] = {"select": {"name": analysis["score"]}}

    # 感恩日记 - 更新已关联记录的标题
    if analysis.get("gratitude") and rels["gratitude"]:
        update_title_record(
            rels["gratitude"][0]["id"], "感恩日记", "感恩日记", analysis["gratitude"]
        )

    # 成功日记 - 更新已关联记录的标题
    if analysis.get("success") and rels["success"]:
        update_title_record(
            rels["success"][0]["id"], "成功日记", "名称", analysis["success"]
        )

    # 总结（已排除其他维度的内容）
    if analysis.get("summary"):
        text = analysis["summary"][:2000]
        update_props["总结"] = {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        }

    # 🍌
    if analysis.get("banana") is not None:
        update_props["🍌"] = {"checkbox": analysis["banana"]}

    # 情绪 - 关联已有记录（支持多个）
    if analysis.get("emotion"):
        emotion_relations = []
        for emotion_name in analysis["emotion"]:
            emotion_page_id = EMOTION_MAP.get(emotion_name)
            if emotion_page_id:
                try:
                    notion_patch(f"/pages/{emotion_page_id}", {
                        "properties": {"创建日期": {"date": {"start": target_date}}}
                    })
                except Exception as e:
                    print(f"[WARN] 更新情绪日期失败: {e}")
                emotion_relations.append({"id": emotion_page_id})
        if emotion_relations:
            update_props["情绪"] = {"relation": emotion_relations}

    # 健康 - 更新已关联记录
    if analysis.get("health") and rels["health"]:
        record_id = rels["health"][0]["id"]
        print(f"[INFO] 更新已有健康记录: {analysis['health']} (id={record_id[-8:]})")
        try:
            update_existing_record(record_id, {"单选": {"select": {"name": analysis["health"]}}})
        except Exception as e:
            print(f"[WARN] 更新健康记录失败: {e}")

    # 睡眠记录 - 更新已关联记录（只改睡眠质量、能量水平、梦境）
    if analysis.get("sleep") and rels["sleep"]:
        sleep_data = analysis["sleep"]
        quality = sleep_data.get("quality")
        energy = sleep_data.get("energy")
        dreams = sleep_data.get("dreams")
        if any([quality, energy, dreams]):
            record_id = rels["sleep"][0]["id"]
            print(f"[INFO] 更新已有睡眠记录: quality={quality} energy={energy} has_dreams={bool(dreams)} (id={record_id[-8:]})")
            props = {}
            if quality:
                props["睡眠质量"] = {"select": {"name": quality}}
            if energy:
                props["能量水平"] = {"select": {"name": energy}}
            if dreams:
                props["梦境记录"] = {"rich_text": [{"type": "text", "text": {"content": dreams[:2000]}}]}
            try:
                update_existing_record(record_id, props)
            except Exception as e:
                print(f"[WARN] 更新睡眠记录失败: {e}")

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
    print(f"[INFO] === 当前时间: {now.strftime('%Y-%m-%d %H:%M')} (UTC+8) ===")

    # 拉取所有笔记
    notes = fetch_getnote_notes()

    # 计算目标日期和时间范围
    # 凌晨的录音归到前一天，所以检查 昨天 00:00 ~ 今天 04:59
    target_date, start, end = get_target_time_range()
    print(f"[INFO] 目标日期: {target_date}，检查范围: {start} ~ {end}")

    if process_date(target_date, start, end, notes):
        print("[INFO] ✅ 全部完成！")
    else:
        print("[INFO] 没有需要处理的录音，跳过。")


if __name__ == "__main__":
    main()
