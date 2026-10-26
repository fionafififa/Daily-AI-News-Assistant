# T02：用 Alembic 管理正式表结构

第一份迁移 `0001` 创建 PostgreSQL 的 `users` 表；当前新增迁移 `0002`，建立任务、工具记录和通知表。`upgrade head` 会按顺序执行到最新版本。关联表及其约束见 [关联表说明](t02-related-tables.md)。

上一轮的 `t02_subscription_demo` 是独立练习表。新的 `users` 表对应应用现有的订阅数据：

| 字段 | 内容 | 示例 |
|---|---|---|
| `id` | 稳定的订阅编号，后续任务通过它关联订阅 | UUID 的十六进制字符串 |
| `profile` | JSONB 格式的完整订阅偏好 | 名称、身份、话题、关键词、推送时间等 |
| `created_at` | 带时区的创建时间 | UTC 时间 |

`JSONB` 保存结构化 JSON，读写时可以对应 Python 字典。这个步骤延续旧订阅结构，方便后续迁移数据和接入业务。

## 执行

在 PowerShell 中运行以下命令。`DATABASE_URL` 只在当前终端生效；也可以把这一行配置保存到本地 `.env`。

```powershell
cd D:\Develop\CODES\Daily-AI-News-Assistant\ai-news-agent
$env:DATABASE_URL = 'postgresql+psycopg://ai_news:ai_news_dev@127.0.0.1:5432/ai_news'
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
```

预期当前版本为：

```text
0002 (head)
```

- `upgrade head`：按顺序执行尚未应用的迁移，直到最新版本。重复执行同一命令不会重复建表。
- `current`：读取当前数据库的迁移版本。
- `alembic_version`：Alembic 用来保存当前版本号的表。

查看正式订阅表：

```powershell
docker exec ai-news-postgres psql -U ai_news -d ai_news -c '\d users'
```

检查表结构定义与数据库是否一致：

```powershell
.\.venv\Scripts\python.exe -m alembic check
```

## 文件关系

| 文件 | 作用 |
|---|---|
| `app/db_schema.py` | 描述当前希望数据库具备的四张正式表结构 |
| `migrations/versions/0001_create_users.py` | 记录从空库到版本 `0001` 的建表步骤 |
| `migrations/versions/0002_create_run_tables.py` | 记录从 `0001` 到 `0002` 的三张关联表及约束 |
| `migrations/env.py` | 从环境变量或 `.env` 取得连接地址，执行迁移 |
| `alembic.ini` | 配置迁移目录和日志 |

`upgrade()` 执行当前版本的修改，`downgrade()` 撤销该版本的修改。本次迁移的撤销操作是删除 `users` 表，会删除表内数据，所以回退验收使用独立临时数据库。

以后修改表结构时，增加新的迁移文件。自动生成的迁移需要审查，再执行；独立练习表不参与自动生成的结构比较。

订阅、任务、工具记录和通知的 PostgreSQL 读写与完整应用接入已完成，见 [完整业务接入说明](t02-postgres-application.md)。当前通过 `DATABASE_URL` 选择后端。用户在 2026-10-26 明确取消 SQLite 旧数据导入，旧文件保留，T02 按调整后的范围收尾。

参考：[Alembic 教程](https://alembic.sqlalchemy.org/en/latest/tutorial.html)、[迁移自动生成与审查](https://alembic.sqlalchemy.org/en/latest/autogenerate.html)。
