# T01：异步请求与错误处理验收

日期：2026-10-23（Asia/Shanghai）。环境：Windows、Python 3.12.13。

状态：T01 已完成。本轮完成来源下载、工具调用、旧测试和检查脚本的异步适配，以及有界重试、取消清理和全量回归。

## 交付结果

| 工作 | 完成行为 | 验证证据 |
|---|---|---|
| 异步下载 | `NewsClient.download/fetch/read` 均为异步；流式读取使用 `async with` 和 `async for` | `tests/test_sources.py`、`tests/test_article_time.py` |
| 工具调用 | 异步工具注册到 `StructuredTool.coroutine`，调用方等待 `ainvoke`；同步工具沿用同步入口 | `tests/test_agent.py`、`tests/test_tools.py`、demo API 与服务测试 |
| 工具执行控制 | 异步包装器包含任务有效性、调用预算和执行记录；等待网络期间不持有工具锁 | `app/tools.py` 的 `async_wrapped`；工具调用和日志集成测试 |
| 正文补读 | 超时耗尽后保留 RSS，并标记 `rss_only`；后续成功补读清除旧警告 | `tests/test_article_reading.py` |
| 自动重试 | 调用一次下载，内部最多尝试 3 次；仅重试 HTTPX 超时，最后一次失败继续抛出 | `tests/test_sources.py` 的超时成功、耗尽和混合错误测试 |
| 总时间限制 | 30 秒异步截止时间覆盖全部尝试、重定向和重试间隔 | 下载中、重试等待中的截止时间测试 |
| 取消与资源释放 | 下载和重试等待都可取消；响应及客户端关闭，取消向工具调用者传播 | 来源取消测试、正文工具取消测试 |
| 旧测试适配 | 模拟客户端和直接调用点遵守异步接口，保留原有筛选、证据和归档断言 | `test_working_context`、`test_prefilter`、`test_token_benchmark` 等 |
| 来源检查脚本 | 使用异步调用，最多同时检查 5 个来源；输出顺序、字段以及失败和空来源的区分保持一致 | 新增 `tests/test_check_sources.py`；真实 RSS 检查 |
| 冻结数据验收脚本 | `FrozenNews`、`SnapshotNews` 使用异步接口；预筛选验收 CLI 可运行 | 脚本相关测试及 4 种组合的 CLI 验收 |

## 下载逻辑与取舍

`download()` 管理总时限、HTTP 客户端和重试；`_download_once()` 执行一轮含重定向的完整下载。每轮使用原始 URL，避免沿用上轮被重定向改写的地址。

- 首次请求加最多两次重试，共最多尝试 3 次。
- 仅捕获 `httpx.TimeoutException` 进行重试；默认间隔为 0.2 秒、0.4 秒，使用 `await asyncio.sleep()`。
- HTTPX 的阶段超时维持 12 秒；下载整体另有 30 秒截止时间，到期抛出 `TimeoutError`。
- HTTP 状态错误、允许域名校验失败、重定向超限和响应超限直接失败。RSS/正文解析错误发生在下载后，也不触发网络重试。
- 每轮最多发出 4 次 HTTP 请求（包括重定向），每一跳都检查允许域名；正文响应最大 2 MB。
- 失败尝试已经读取的部分字节被丢弃，下一次响应重新累计字节数。
- `CancelledError` 不转换成普通错误或 RSS 降级结果；退出上下文管理器时清理响应和客户端。

本轮没有新增依赖。`download_timeout` 和 `retry_delay` 是 `NewsClient` 的可选关键字参数；测试可缩短截止时间或关闭等待，生产调用沿用默认值。

## 自动化验收

最终全量结果：**127 passed in 14.51s**。运行时警告按错误处理，结果无协程未等待警告。

静态检查通过；格式检查结果为 **35 files already formatted**。

demo 已通过真实 FastAPI `TestClient` 验证创建订阅、请求生成、等待任务完成、读取简报和历史；服务测试另覆盖站内通知和历史去重。模型接入测试使用模拟 HTTP，实际执行 LangChain 的 Agent 和工具循环。

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning
.\.venv\Scripts\python.exe -m ruff check app tests scripts
.\.venv\Scripts\python.exe -m ruff format --check app tests scripts
.\.venv\Scripts\python.exe -m scripts.check_sources
.\.venv\Scripts\python.exe -m scripts.prefilter_acceptance --output "$env:TEMP/t01-prefilter-proof.json" --requests-output "$env:TEMP/t01-prefilter-requests.json"
```

## 脚本实际运行结果

2026-10-23 16:42（Asia/Shanghai），来源检查脚本正常结束，5 个来源均为 `ok`。当次返回的最近 24 小时候选数如下；每个来源最多返回 40 条，数量会随实际运行时间变化。

| 来源 | 候选数 |
|---|---:|
| OpenAI | 2 |
| Google AI | 0 |
| Hugging Face | 2 |
| TechCrunch | 13 |
| arXiv | 40 |

Google AI 的空结果保留为成功，不被转换为来源失败。

冻结数据验收 CLI 的输出和请求归档均通过 JSON 解析。输出放在临时目录，没有覆盖已有历史验收材料。

| 预筛选 | 工作上下文整理 | 原始条目 | 保留条目 | 模型协议请求轮次 | 工具调用 |
|---|---|---:|---:|---:|---:|
| 开 | 开 | 25 | 8 | 9 | 8 |
| 关 | 开 | 25 | 25 | 9 | 8 |
| 开 | 关 | 25 | 8 | 9 | 8 |
| 关 | 关 | 25 | 25 | 9 | 8 |

这四组使用模拟模型响应验证协议和材料，不涉及付费模型调用。真实付费模型的摘要质量、Token 消耗和外部邮件送达不属于本次 T01 验收范围。
