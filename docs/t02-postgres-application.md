# T02：PostgreSQL 完整业务接入

本轮目标：把已有四张正式表接入网页和生成服务，完成“保存订阅 → 生成任务 → 工具记录 → 简报 → 通知”的完整业务链路。

## 启动与后端选择

本地 `.env` 已补上 PostgreSQL 的 `DATABASE_URL`，其他原有配置保持不变。启动 Docker Desktop 和已有 PostgreSQL 容器后，在项目目录运行：

```powershell
cd D:\Develop\CODES\Daily-AI-News-Assistant\ai-news-agent
docker start ai-news-postgres
.\.venv\Scripts\python.exe -m alembic upgrade head
./run.ps1
```

打开 `http://127.0.0.1:8765`。访问 `/health` 或 `/api/status`，其中 `storage_backend` 应为 `postgresql`。这个字段表示选择的存储后端，不表示每次访问都重新探测数据库连接。

`DATABASE_URL` 为空时仍可使用 SQLite。配置 PostgreSQL 后，数据库连接或表结构不满足要求就明确失败，不会静默读取另一套 SQLite 数据。

`.env.example` 默认留空连接地址，保留无需额外服务即可运行的本地演示入口。其他环境要使用 PostgreSQL，设置连接地址并执行迁移即可。

## 代码如何连接起来

```text
Settings.load() 读取 DATABASE_URL
  → create_store(settings) 选择 PostgresStore
  → 启动时检查迁移版本并处理上次中断的任务
  → FastAPI 与 NewsService 通过同一套 Store 接口读写四张表
  → 关闭时等待任务与数据库操作，再释放连接池
```

| 文件 | 职责 |
|---|---|
| `app/postgres_store.py` | 任务、工具记录、通知、重启恢复和原子推送抢占 |
| `app/postgres_subscriptions.py` | 复用上一轮的新建、查询、修改与列出订阅 |
| `app/storage.py` | SQLite、公共序列化，以及统一后端工厂 |
| `app/database.py` | 连接地址、连接池与 SQL/锁等待时限 |
| `app/async_storage.py` | 同步 SQL 的线程执行与取消处理 |
| `app/main.py` | API 接入、启动检查、SSE 与安全错误响应 |
| `app/service.py` | 异步入队、生成、调度和推送流程 |
| `app/tools.py` | 异步工具的执行记录在线程中持久化 |

本轮没有新增 Python 第三方依赖。浏览器验收补齐了当前 Playwright 所需的本机 Chromium 运行时。

## 一次“立即生成”的执行顺序

1. 接口查询订阅，确认编号存在。
2. `await service.enqueue(user_id)` 为这次生成申请任务记录。
3. `PostgresStore.new_run()` 在事务中插入一条 `queued` 任务。
4. 数据库检查订阅外键、同日任务唯一规则和活动任务唯一规则。
5. 提交成功后返回任务，接口响应 `202`，生成服务启动后台执行。
6. 服务保存 `running` 状态，建立任务工作目录并调用 Agent。
7. 工具调用通过 `trace()` 保存参数、结果、状态和耗时。
8. 研究与摘要复核完成后，事务保存简报和 `completed` 状态。
9. 推送流程领取任务、写站内通知、按配置发送邮件，最后更新推送状态。
10. SSE 按游标向页面发送持久化通知；重连时继续读取游标之后的记录。

PostgreSQL 中的日期和时间在 Python 中是对象。`decode_record()` 在存储层边界将它们转换成 ISO 字符串，JSONB 简报和参数仍是字典，从而保持原有 API、历史去重和邮件渲染所用的数据形状。

日志参数继续遵守 24000 字符的记录边界，过长参数保存为合法的 JSON 预览对象；返回结果保留原有的有界文本形式。

## 错误、重复与重试

| 情况 | 处理 |
|---|---|
| 活动任务或同日任务冲突 | `new_run()` 返回 `None`，手动请求响应 `409` |
| 任务引用不存在的订阅 | 映射为 `KeyError`，API 响应 `404` |
| 其他意外的数据库完整性错误 | 继续抛出，不伪装成“已有任务” |
| HTTP 请求中的数据库错误 | 返回 `503` 和通用说明；日志只记录异常类别，不返回 SQL 参数或连接密码 |
| 未迁移或版本不匹配 | 明确提示执行 `python -m alembic upgrade head`，启动失败 |
| 邮件失败 | 简报保留为 `completed`，推送标记 `failed`；重试复用简报与工具记录 |
| 生成中关闭或重启 | 按原业务标记中断，不声称已恢复 Agent 执行进度 |
| 推送中断 | 标记为 `unknown`，核对收件箱后决定是否重试 |

推送领取使用一条带状态条件的 `UPDATE ... RETURNING`：只有已完成且推送状态为 `pending`、`failed` 或 `unknown` 的任务才能变成 `sending`。两条连接并发领取时只有一条成功。

站内通知使用 `ON CONFLICT DO NOTHING` 与任务编号唯一约束，重试不会生成重复通知。SMTP 仍不能保证跨故障的严格 exactly-once。

## 同步 SQL 与异步服务之间的边界

SQLAlchemy 当前使用同步驱动。FastAPI 的普通 `def` 路由由框架在线程池执行；`async def` 路由、后台协程和异步工具通过 `call_store()` 将同步操作放入工作线程：

```python
run = await call_store(self.store.run, run_id)
```

等待数据库时，事件循环可以处理其他请求。连接池复用连接，连接等待、语句执行和锁等待都设置了时限。

取消协程无法强制停止线程中已经运行的 SQL。`call_store()` 等待该操作结束后再向外传播取消，避免清理任务状态后，原来的写入才到达数据库。连续两次取消也有测试覆盖。

申请任务由服务管理。HTTP 请求断开后，已接受的任务申请仍会完成；关闭服务时等待申请结束，将未开始执行的任务标记中断，再关闭连接池。`enqueue()` 现在需要 `await`，相关测试与独立验收脚本已经适配。

## 复现验证

```powershell
$env:TEST_POSTGRES_URL = 'postgresql+psycopg://ai_news:ai_news_dev@127.0.0.1:5432/ai_news'
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning
.\.venv\Scripts\python.exe -m ruff check app tests scripts migrations
.\.venv\Scripts\python.exe -m ruff format --check app tests scripts migrations
```

测试使用独立临时数据库，覆盖存储方法、两种后端的 API 与服务、并发推送领取、通知游标、重启恢复、邮件失败重试、数据库错误响应，以及取消和关闭竞态。

浏览器验证使用独立临时数据库和 DEMO 服务，覆盖页面保存、两次生成、SSE、修改偏好、历史、刷新，以及真正停止和重新启动服务后的持久化；没有调用付费模型或真实邮箱。

本轮证据见 [完整接入验收](t02-application-acceptance-2026-10-26.md)。

## 阶段收尾与学习目标

网页和生成服务现在已统一支持 PostgreSQL，T02 按当前范围完成。2026-10-26 用户考虑开发成本，明确取消 SQLite 旧数据导入，不再为此开发代码。旧文件保留，不因切换配置自动复制或删除历史；旧历史不会出现在 PostgreSQL 的查询结果中。

后台调度仍采用单进程方式，部署继续使用一个 Uvicorn worker；独立队列与多进程协调属于后续 T03。

本轮核心能力：保持存储接口一致、划分事务、精确处理唯一冲突，以及管理同步 SQL 与异步服务的边界。工程验证通过不等于用户已独立掌握，观察单独记录在学习记录中。

后续可选独立练习：为历史列表接口增加 `limit` 查询参数，允许 1–100，传给已有的 `store.runs(user_id, limit)`。先说明成功和无效参数的预期，再实现；不影响本阶段收尾。

参考：[SQLAlchemy PostgreSQL 冲突处理](https://docs.sqlalchemy.org/en/21/dialects/postgresql.html#insert-on-conflict-upsert)、[Python 线程与取消保护](https://docs.python.org/3.12/library/asyncio-task.html)、[FastAPI 同步与异步入口](https://fastapi.tiangolo.com/async/#path-operation-functions)。
