# Get 笔记 → Notion 每日总结自动同步

每天凌晨 2:00 自动从 Get 笔记拉取前一天的「每日总结」，写入 Notion 日记中心。

## 工作原理

1. 从 Get 笔记 API 拉取笔记列表
2. 筛选前一天标题或内容包含「每日总结」的笔记
3. 在 Notion 日记中心找到前一天的记录
4. 将总结内容写入「总结」字段

## 使用方法

在 Get 笔记中录音，开头说「每日总结」，然后自由讲述当天的事。

## Secrets

| 名称 | 说明 |
|------|------|
| `GETNOTE_API_KEY` | Get 笔记 API Key |
| `GETNOTE_CLIENT_ID` | Get 笔记 Client ID |
| `NOTION_TOKEN` | Notion Integration Token |
| `NOTION_DB_ID` | Notion 日记中心数据库 ID |
