# Get 笔记 → Notion 每日总结自动同步

每天自动从 Get 笔记拉取前一天的「每日总结」，分析后写入 Notion 日记中心。

## 工作原理

1. 从 Get 笔记 API 拉取笔记列表
2. 筛选前一天标题或内容包含「每日总结」的笔记
3. 在 Notion 日记中心找到前一天的记录
4. 将总结内容写入「总结」字段
5. 如果识别到情绪，则按既有情绪模板新建当天情绪页面并关联回日记
6. 如果识别到健康并带有补充说明，则把补充内容一并回写到健康记录标题

## Harness 化约束

这条 workflow 现在按轻量 harness 方式运行：

- `workflow`：筛选录音、合并原文、AI 分析、Notion 查找、Notion 写回
- `state`：每次运行都会写 `.harness/latest-run.json`、`.harness/workflow-state.json` 和 `.harness/runs/*.json`
- `checkpoint`：至少记录 `preflight`、`fetch_notes`、`note_selection`、`analysis`、`notion_lookup`、`notion_write`
- `eval`：支持用固定样例 `eval_cases.json` 做 AI 解析回归

## 本地运行

- 正常同步：`python sync.py`
- 只跑流程不写回 Notion：`python sync.py --dry-run`
- 跑固定样例评估：`python sync.py --eval-fixture eval_cases.json`

## 使用方法

在 Get 笔记中录音，开头说「每日总结」，然后自由讲述当天的事。

## Secrets

| 名称 | 说明 |
|------|------|
| `GETNOTE_API_KEY` | Get 笔记 API Key |
| `GETNOTE_CLIENT_ID` | Get 笔记 Client ID |
| `NOTION_TOKEN` | Notion Integration Token |
| `NOTION_DB_ID` | Notion 日记中心数据库 ID |
| `DEEPSEEK_API_KEY` | DeepSeek 分析接口 Key |
