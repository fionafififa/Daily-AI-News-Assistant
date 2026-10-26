# T02 第一张正式订阅表验收

日期：2026-10-24，Asia/Shanghai。

## 本轮交付

- 引入 Alembic 1.20.1，并同步项目依赖、锁文件和导出的运行依赖。
- 新增 `app/db_schema.py`，以 SQLAlchemy 描述 `users` 表的目标结构。
- 新增 Alembic 配置、迁移环境、版本模板，以及 `0001_create_users.py`。
- 为 `.env.example` 增加 `DATABASE_URL` 示例；迁移进程读取当前终端环境变量或本地 `.env`。
- 已在本地 `ai_news` 数据库执行升级，`alembic current` 返回 `0001 (head)`。

正式表结构：

| 字段 | PostgreSQL 类型 | 约束 |
|---|---|---|
| `id` | `text` | 主键、非空 |
| `profile` | `jsonb` | 非空 |
| `created_at` | `timestamp with time zone` | 非空 |

## 实际验证

临时数据库 `t02_migration_check_e390874e4800` 用于验证，完成后已删除。回退验证只作用于这个临时数据库。

| 检查 | 实际结果 |
|---|---|
| 空库执行 `upgrade head` | 正式表和版本记录建立成功 |
| 列、主键、非空及带时区类型检查 | 与目标结构一致 |
| 订阅偏好字典写入、提交后重新连接读取 | JSONB 字段及时间字段正常往返 |
| 插入后故意抛异常 | 对应记录被回滚，先前已提交记录保留 |
| 对已升级数据库再次执行升级 | 已有数据保留，未重复建表 |
| `alembic check` | `No new upgrade operations detected.` |
| 独立练习表存在时进行结构比较 | 未产生删除练习表的迁移 |
| 临时数据库回退到 `base` | 仅撤销本次管理的订阅表，独立练习表保留 |
| 回退后再次升级 | 正式表可重新建立 |
| 本地数据库实际升级及 `psql` 查看结构 | 已建立 `users`，版本为 `0001` |
| 全量原有业务测试 | `127 passed in 17.85s` |
| 新增 Python 文件的 Ruff 检查与格式检查 | 全部通过 |

测试命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning
.\.venv\Scripts\python.exe -m ruff check app/db_schema.py migrations
.\.venv\Scripts\python.exe -m ruff format --check app/db_schema.py migrations
```

迁移执行与复核命令见 [T02 迁移说明](t02-postgres-migrations.md)。

## 范围与后续

这次交付完成一张正式表和结构版本管理。当前应用仍通过原有 `Store` 访问 SQLite；其余正式表、订阅业务读写接入和 SQLite 历史数据导入属于后续小交付。

工程检查通过不等于学习验收通过。用户对迁移版本、事务及恢复边界的理解仍需独立操作验证，能力观察记录在 [学习记录](learning-progress.md)。
