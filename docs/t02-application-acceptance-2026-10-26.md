# T02 PostgreSQL 完整业务接入验收

日期：2026-10-26，Asia/Shanghai。

## 本轮交付

- 实现完整 `PostgresStore`：任务创建、查询、更新、历史、执行记录、站内通知、推送领取和重启恢复。
- `Settings.database_url` 与 `create_store()` 统一选择后端；网页和生成服务使用相同存储。
- 本地 `.env` 只补充 `DATABASE_URL`，核对其他配置值保持不变；该文件仍由 Git 忽略。
- `DATABASE_URL` 为空时支持 SQLite；配置 PostgreSQL 后数据库出错会明确失败，不自动换库。
- 同步数据库操作通过异步调用边界执行；覆盖取消、重复取消、HTTP 请求断开和关闭期间申请任务的场景。
- API 增加后端类型标识及通用数据库错误响应；不返回连接地址或 SQL 参数。
- 未新增 Python 第三方依赖；为浏览器验收安装当前 Playwright 对应的 Chromium Headless Shell 运行时。

## 自动化检查

完整测试结果：`174 passed in 28.05s`，真实 PostgreSQL 用例全部启用。

覆盖内容包括：

- 原有 Agent、工具、摘要复核、来源、API、调度和推送测试。
- SQLite 与 PostgreSQL 两套 API 和服务流程。
- 任务日期、时间和结构化简报的兼容返回格式。
- 活动任务、同日任务冲突的精确映射；未知订阅与意外完整性错误分别处理。
- 失败任务保留历史，并释放新的手动任务名额。
- 日志参数和结果的长度边界。
- 两条独立连接抢占推送时仅一条成功。
- 通知去重、游标补收及订阅隔离。
- 重新创建应用后，订阅、简报、工具记录和通知仍保留。
- 邮件失败后通过 API 单独重试，简报和工具记录不变，站内通知不重复。
- 缺少迁移时启动失败；数据库请求错误返回 `503`，私有 SQL 参数不进入响应。
- 工作线程执行同步 SQL；取消与连续两次取消均等待当前操作结束。
- HTTP 请求取消后已接受任务继续交给服务；服务关闭等待申请结束并标记中断。

全项目检查结果：

```text
ruff check app tests scripts migrations: All checks passed!
ruff format --check app tests scripts migrations: all files already formatted
```

对两个已有学习脚本只做格式整理，并比较整理前后的 Python 语法树，确认调用内容和参数保持不变。

## 浏览器与进程重启

使用独立临时 PostgreSQL 数据库、独立工作目录和 DEMO 服务运行 Chromium 验收。12 项检查通过，浏览器错误列表为空：

1. 创建订阅。
2. 生成简报。
3. 偏好筛选。
4. 排除词。
5. SSE 推送与通知游标。
6. 工具执行记录。
7. 桌面页面。
8. 手机页面无横向溢出。
9. 修改偏好。
10. 历史简报切换。
11. 刷新后偏好与历史保留。
12. 停止并重新启动 Uvicorn 进程后，两份历史记录及订阅仍一致。

验证期间 `storage_backend` 为 `postgresql`，独立工作目录中没有创建 `news.sqlite3`。没有调用付费模型或向真实邮箱发送邮件。

证据：

- [浏览器检查 JSON](t02-postgres-app-browser-20261026-4089e0/proof.json)
- [桌面截图](t02-postgres-app-browser-20261026-4089e0/preview-desktop.png)
- [手机截图](t02-postgres-app-browser-20261026-4089e0/preview-mobile.png)
- [测试服务日志](t02-postgres-app-browser-20261026-4089e0/server.log)

测试临时数据库与临时工作目录已清理。浏览器证据保存在新目录，旧验收材料未覆盖。

## 复跑命令

```powershell
$env:TEST_POSTGRES_URL = 'postgresql+psycopg://ai_news:ai_news_dev@127.0.0.1:5432/ai_news'
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning
.\.venv\Scripts\python.exe -m ruff check app tests scripts migrations
.\.venv\Scripts\python.exe -m ruff format --check app tests scripts migrations
```

浏览器脚本也可对独立启动的 DEMO 服务执行，截图应指定新的目录：

```powershell
.\.venv\Scripts\python.exe -m scripts.browser_smoke --url http://127.0.0.1:8765 --screenshots docs/新的验收目录
```

脚本会在目标服务中新建测试订阅；本次验收使用临时数据库隔离了这些数据。

## 阶段收尾与范围边界

- 2026-10-26 收尾复查：启用真实 PostgreSQL 用例，全量测试 `174 passed in 27.54s`；Ruff 检查通过，51 个文件均符合格式。沿用上面的 12 项浏览器验收证据，本次收尾只修改文档，未重复运行浏览器流程。
- T02 已按调整后的范围完成：PostgreSQL 表结构与版本管理、完整存储接口、业务接入及验收均已交付。
- 2026-10-26 用户考虑开发成本，明确取消 SQLite 旧数据导入，不再为此开发代码，也不作为遗留任务。原文件仍保留，PostgreSQL 页面不显示旧 SQLite 历史。
- 继续使用单个 Uvicorn worker；进程外队列、独立调度及多进程恢复属于 T03。
- Agent 仍可能产生事实错误，原有复核和来源证据机制继续使用；本轮不宣称提高了模型摘要质量。
- 本轮代码与验收由 Codex 完成，学习情况单独记录，不以测试通过替代用户独立掌握。

操作步骤、调用顺序及取舍见 [完整业务接入说明](t02-postgres-application.md)。
