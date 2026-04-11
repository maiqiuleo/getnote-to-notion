# Get 笔记 → Notion 碎片中心 自动同步

自动将 Get 笔记中的内容同步到 Notion 碎片中心数据库。

## 功能特点

- ✅ **全量同步**：同步所有类型的笔记（文字、录音转文字、图片 OCR）
- ✅ **防重复**：基于 Get 笔记 ID 记录，避免重复同步
- ✅ **定时检查**：每 10 分钟自动检查新笔记
- ✅ **外部触发**：支持 repository_dispatch 外部触发
- ✅ **完整元数据**：保留创建时间、标签、来源链接

## 配置

### 1. GitHub Secrets

在仓库 Settings → Secrets and variables → Actions 中添加：

| 变量名 | 说明 | 示例 |
|--------|------|------|
| `GETNOTE_API_KEY` | Get 笔记 API Key | `gk_live_xxx...` |
| `GETNOTE_CLIENT_ID` | Get 笔记 Client ID | `cli_xxx...` |
| `NOTION_TOKEN` | Notion Integration Token | `ntn_xxx...` |
| `NOTION_DB_ID` | 碎片中心数据库 ID | `11233b33-...` |

### 2. GitHub Variables（可选）

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `CHECK_HOURS` | 检查最近 N 小时的笔记 | `2` |

## 同步触发方式

### 方式一：GitHub Schedule（内置，每10分钟）

```yaml
schedule:
  - cron: '*/10 * * * *'
```

### 方式二：repository_dispatch（推荐，稳定）

使用外部 cron 服务（如 cron-job.org）定期触发：

```bash
POST https://api.github.com/repos/{owner}/{repo}/dispatches
Authorization: Bearer {GITHUB_PAT}
Content-Type: application/json

{
  "event_type": "sync"
}
```

### 方式三：手动触发

在 GitHub Actions 页面手动点击 "Run workflow"。

## 本地测试

```bash
export GETNOTE_API_KEY="your_api_key"
export GETNOTE_CLIENT_ID="cli_xxx"
export NOTION_TOKEN="your_notion_token"
export NOTION_DB_ID="11233b33-..."
export CHECK_HOURS="2"

python sync.py
```

## 工作原理

1. **拉取笔记**：从 Get 笔记 API 获取最近更新的笔记列表
2. **时间过滤**：只处理最近 N 小时（默认2小时）内的笔记
3. **去重检查**：跳过已同步的笔记
4. **获取详情**：拉取每条笔记的完整内容（包括录音转文字）
5. **创建页面**：在 Notion 碎片中心创建新页面

## 数据结构映射

| Get 笔记 | Notion 碎片中心 |
|----------|-----------------|
| 标题 | Name（标题） |
| 内容 | 页面正文 |
| 创建时间 | 创建时间 |
| 标签 | Tags |
| 笔记 ID | Link（来源链接） |

## 隐私说明

- 仓库设为公开，Secrets 不会在日志中泄露
- 日志中只输出笔记标题摘要，不输出完整内容
- 已同步记录存储在 `processed_ids.json`

## 故障排查

### 同步失败

1. 检查 Secrets 是否正确配置
2. 检查 Notion 数据库是否有正确的字段（Name、Tags、Link、创建时间、删除）
3. 查看 GitHub Actions 日志获取详细错误信息

### 重复同步

如果发生重复同步，可以：
1. 手动删除 `processed_ids.json` 缓存
2. 或重新运行 workflow 重建缓存

## 许可证

MIT
