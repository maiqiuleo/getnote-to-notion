# Get笔记 → Notion 同步配置指南

## 概述

本系统可以自动将 Get 笔记中的内容同步到 Notion 碎片中心数据库。

## 快速开始

### 1. 配置 GitHub Secrets

访问 `https://github.com/Eddiehhhhh/getnote-to-notion/settings/secrets/actions`

添加以下 Secrets：

| Secret 名称 | 值 | 说明 |
|------------|-----|------|
| `GETNOTE_API_KEY` | `gk_live_...` | Get 笔记 API Key |
| `GETNOTE_CLIENT_ID` | `cli_...` | Get 笔记 Client ID |
| `NOTION_TOKEN` | `ntn_...` | Notion Integration Token |
| `NOTION_DB_ID` | `11233b33-...` | 碎片中心数据库 ID |

### 2. 配置 GitHub Variables（可选）

访问 `https://github.com/Eddiehhhhh/getnote-to-notion/settings/variables/actions`

| Variable 名称 | 值 | 说明 |
|--------------|-----|------|
| `CHECK_HOURS` | `2` | 检查最近 N 小时的笔记 |

### 3. 手动触发测试

访问 `https://github.com/Eddiehhhhh/getnote-to-notion/actions`

点击 "Sync Get笔记 to Notion" → "Run workflow" 手动触发同步。

## 获取配置信息

### Get 笔记 API Key & Client ID

1. 访问 https://www.biji.com/openapi
2. 点击「创建应用」
3. 填写应用名称（如 "Notion同步"）
4. 复制 **Client ID** 和 **API Key**

### Notion Integration Token

1. 访问 https://www.notion.so/my-integrations
2. 点击 "New integration"
3. 填写名称，选择关联的工作区
4. 复制 **Internal Integration Token**

### Notion 数据库 ID

1. 打开 Notion 碎片中心数据库
2. 复制数据库页面的 URL
3. 提取数据库 ID：
   - URL 格式: `https://www.notion.so/xxx?v=...`
   - 或: `https://www.notion.so/xxx?p=...`
   - 数据库 ID 是 URL 中 `notion.so/` 后面的一串字符（去掉参数）

例如：
- URL: `https://www.notion.so/11233b33-7f23-8024-9555-cb8de8c58e02?v=...`
- 数据库 ID: `11233b33-7f23-8024-9555-cb8de8c58e02`

### 授权 Notion Integration 访问数据库

1. 在 Notion 中打开碎片中心数据库
2. 点击右上角 "..." → "Add connections"
3. 选择你创建的 Integration
4. 确认授权

## 同步触发方式

### 方式一：自动定时（每10分钟）

已配置在 `.github/workflows/sync.yml` 中：

```yaml
schedule:
  - cron: '*/10 * * * *'
```

### 方式二：外部触发（推荐，更稳定）

使用 cron-job.org 等外部服务触发：

```bash
POST https://api.github.com/repos/Eddiehhhhh/getnote-to-notion/dispatches
Authorization: Bearer {GITHUB_PAT}
Content-Type: application/json

{
  "event_type": "sync"
}
```

### 方式三：手动触发

在 GitHub Actions 页面点击 "Run workflow"。

## 数据结构

### Get 笔记 → Notion 映射

| Get 笔记字段 | Notion 字段 | 说明 |
|-------------|------------|------|
| `id` | Link | 笔记来源链接 |
| `title` | Name | 笔记标题 |
| `content` / `transcript` | 页面正文 | 笔记内容 |
| `created_at` | 创建时间 | 笔记创建时间 |
| `tags` | Tags | 笔记标签 |

## 故障排查

### 问题：同步失败，日志显示 API 错误

**检查步骤：**
1. 确认 Secrets 是否正确配置
2. 确认 Notion Integration 已授权访问数据库
3. 检查 GitHub Actions 日志获取详细错误信息

### 问题：笔记没有同步到 Notion

**检查步骤：**
1. 确认 Get 笔记 API Key 对应的账号有笔记
2. 检查笔记创建时间是否在 CHECK_HOURS 范围内
3. 查看 processed_ids.json 是否已记录该笔记

### 问题：重复同步

**解决方案：**
1. 删除 GitHub Actions 缓存中的 processed_ids.json
2. 或修改代码清空 processed_ids.json

### 问题：GitHub Actions 被暂停

**原因：** GitHub 会自动暂停 60 天无活动的仓库的 Actions。

**解决方案：**
1. 手动触发一次 workflow
2. 或使用外部触发方式（repository_dispatch）

## 本地测试

```bash
# 克隆仓库
git clone https://github.com/Eddiehhhhh/getnote-to-notion.git
cd getnote-to-notion

# 设置环境变量
export GETNOTE_API_KEY="your_api_key"
export GETNOTE_CLIENT_ID="cli_xxx"
export NOTION_TOKEN="your_notion_token"
export NOTION_DB_ID="11233b33-..."
export CHECK_HOURS="2"

# 运行同步
python sync.py
```

## 更新日志

### v1.0.0
- 初始版本
- 支持全量同步 Get 笔记到 Notion
- 支持防重复机制
- 支持定时触发和外部触发
