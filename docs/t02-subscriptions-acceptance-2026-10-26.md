# T02 正式订阅读写验收

日期：2026-10-26，Asia/Shanghai。

## 交付内容

- 新增 `app/database.py`，共享 PostgreSQL 连接配置，迁移环境与运行脚本使用相同的地址读取逻辑。
- 新增 `app/postgres_subscriptions.py`，使用 SQLAlchemy 操作已由 Alembic 建立的 `users` 表。
- 新增 `scripts/check_postgres_subscriptions.py`，实际新建一份完整订阅，再重新取得连接查询并核对结果。
- 新增 `tests/test_postgres_subscriptions.py`，显式启用后在独立临时 PostgreSQL 数据库执行真实迁移和读写验收。
- 增加详细的 [执行顺序与验证说明](t02-subscription-storage.md)，并更新学习记录。

本轮没有新增第三方依赖。

## 行为约定

| 行为 | 约定 |
|---|---|
| 新建 | 校验完整偏好，生成文本 UUID 编号，记录 UTC 创建时间，提交后返回完整订阅 |
| 查询 | 编号存在返回订阅，不存在返回 `None` |
| 修改 | 完整替换偏好，保留原编号与创建时间；未知编号抛出 `KeyError` |
| 列表 | 返回按创建时间、编号排序的列表，空表返回 `[]` |
| 无效输入 | 由 `Subscription` 校验拒绝，不写入数据 |
| 数据库错误 | 异常向外传播，事务回滚；不转为空结果或成功 |

## 实际验证结果

1. 普通业务回归：`127 passed, 8 skipped in 17.92s`。8 项 PostgreSQL 测试因该次未设置测试连接地址而显式跳过。
2. 设置 `TEST_POSTGRES_URL` 后单独执行 PostgreSQL 集成测试：`8 passed in 2.99s`。
3. 新增和修改的 Python 文件 Ruff 静态与格式检查通过。
4. 本地数据库执行 `alembic upgrade head` 成功，继续使用版本 `0001`，没有额外表结构修改。
5. 独立脚本在正式 `users` 表新建订阅 `2c00c6769cec4f82857188c3fd7dca36` 并查回，内容一致；随后用 psql 独立查询确认 `profile` 是 JSON 对象，创建时间有时区。
6. 查询 `pg_database`，以 `t02_subscriptions_test_` 开头的残留测试数据库数量为 `0`。

集成测试覆盖：

- 中文、单引号、结构化 JSON 偏好及带时区时间的保存和跨连接读取。
- 修改保留编号、创建时间，不影响其他订阅，也不新增重复记录。
- 未知编号的查询与修改行为。
- 空名称、无订阅偏好及注入内部编号等无效输入。
- 重复主键触发错误后的回滚，以及后续写入恢复。
- 按创建时间排序及相同时间下的编号排序。

## 验证命令

在项目目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning
$env:TEST_POSTGRES_URL = 'postgresql+psycopg://ai_news:ai_news_dev@127.0.0.1:5432/ai_news'
.\.venv\Scripts\python.exe -m pytest tests/test_postgres_subscriptions.py -q -W error::RuntimeWarning
.\.venv\Scripts\python.exe -m ruff check app/database.py app/postgres_subscriptions.py scripts/check_postgres_subscriptions.py tests/test_postgres_subscriptions.py migrations
.\.venv\Scripts\python.exe -m ruff format --check app/database.py app/postgres_subscriptions.py scripts/check_postgres_subscriptions.py tests/test_postgres_subscriptions.py migrations
```

测试连接仅用于创建以 `t02_subscriptions_test_` 开头的临时数据库，每个用例通过 Alembic 建表，结束时删除对应临时数据库。现有业务订阅不参与测试数据写入或清理。

## 取舍与剩余范围

- 使用 SQLAlchemy Core 和现有同步 `Store` 一致的方法形式，集中理解数据库读写与事务；异步调用边界会在完整存储切换时复核。
- `profile` 使用既有 JSONB 表结构，保存完整偏好；返回值继续使用页面熟悉的扁平订阅形状。
- 网页和生成服务仍使用 SQLite `Store`。任务表通过外键关联订阅，后续将准备剩余关联表并统一切换，避免跨库关联不一致。
- SQLite 历史数据导入、网页对 PostgreSQL 的端到端运行、数据库故障到 HTTP 响应的映射，属于后续工作。
- 本轮代码与测试由 Codex 完成；工程验证结果不作为用户已经独立掌握数据库读写的证据。待独立练习见 [学习记录](learning-progress.md)。
