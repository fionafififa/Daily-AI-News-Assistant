# T02 关联表与约束验收

日期：2026-10-26，Asia/Shanghai。

## 本轮交付

- 扩展 SQLAlchemy 元数据：新增 `runs`、`traces`、`notifications`。
- 新增 Alembic 迁移 `0002_create_run_tables.py`，保留原有 `0001`。
- 任务保留同日唯一、活动任务唯一规则，增加正式外键和查询索引。
- 通知通过复合外键保证任务与订阅的归属一致，并限制同一任务只有一条站内通知。
- 共享临时 PostgreSQL 测试环境，新增 12 项真实数据库验收；订阅测试继续执行，并核对最新版本 `0002`。
- 本轮没有新增第三方依赖。

## 实际结果

完整回归在设置 `TEST_POSTGRES_URL` 后执行：`147 passed in 21.62s`，包含原有 127 项业务测试、8 项订阅读写测试和本轮 12 项关联表测试。

| 验收内容 | 实际结果 |
|---|---|
| 从空库按 `0001 → 0002` 迁移 | 每个临时数据库均建立成功 |
| 简报、工具参数 JSONB，时间与日期，推送默认状态 | 写入并查回正确，推送默认为 `pending` |
| 空简报 | 按 SQL 空值保存，可用 `IS NULL` 匹配 |
| 不完整的工具返回 JSON | 作为文本原样保存 |
| 无订阅的任务、无任务的工具记录或通知 | 外键拒绝，SQLSTATE `23503` |
| 将任务通知写给其他订阅 | 复合外键拒绝 |
| 同一任务重复写站内通知 | 唯一约束拒绝 |
| 两条独立连接并发插入活动任务 | 排队/排队、排队/运行、运行/运行三组均只有一条成功 |
| 活动任务完成后再手动生成 | 新任务可创建 |
| 同一订阅同一天重复任务 | 被拒绝；不同日期、其他订阅和已完成后的手动任务可创建 |
| 删除有历史关联的父记录 | 外键拒绝 |
| 临时库回退到 `0001` 再升级到最新版本 | 成功，订阅和独立练习表保留，三张新表重新建立 |
| `alembic check` | `No new upgrade operations detected.` |
| 本地 `ai_news` 升级 | 当前版本 `0002 (head)` |
| 本地升级前后订阅记录比对 | 4 条记录的编号、完整偏好与创建时间完全一致 |
| 临时测试数据库清理 | 查询残留数量为 `0` |
| 本轮变更 Python 文件的 Ruff 检查与格式检查 | 全部通过 |

回退会删除 `runs`、`traces`、`notifications` 及其数据，因此实际回退验收仅在临时数据库执行。本地已有订阅数据库只执行升级和只读核对。

## 复跑方法

在项目目录运行：

```powershell
$env:TEST_POSTGRES_URL = 'postgresql+psycopg://ai_news:ai_news_dev@127.0.0.1:5432/ai_news'
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning
.\.venv\Scripts\python.exe -m ruff check app/db_schema.py migrations tests/conftest.py tests/test_postgres_subscriptions.py tests/test_postgres_schema.py
.\.venv\Scripts\python.exe -m ruff format --check app/db_schema.py migrations tests/conftest.py tests/test_postgres_subscriptions.py tests/test_postgres_schema.py
```

## 取舍与后续

- 延续原有生成与调度规则，集中将业务关系和约束落实到 PostgreSQL。
- 日志参数使用 JSONB，可能被截断的返回结果继续使用文本，避免丢失已有执行证据。
- 归属一致性在数据库层执行，避免通知只检查订阅、任务分别存在，却把二者错误组合。
- 网页及生成服务尚未切换；下一小交付是 PostgreSQL 完整存储方法与业务接入，然后处理历史数据导入。
- 本轮迁移与测试由 Codex 实现，用户的独立学习情况单独记录在 [学习记录](learning-progress.md)。

详细说明见 [关联表与业务约束](t02-related-tables.md)。
