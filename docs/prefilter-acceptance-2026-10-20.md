# 信息预筛选验收报告

验收日期：2026-10-20。结论：本次确定的六项验收标准全部通过。信息预筛选默认启用，原有按状态重建上下文的功能保留，最终提交校验保留。

本次是确定性筛选和模型调用协议的验收。新闻、历史和模型 HTTP 返回均使用固定测试材料；执行了真实 `ChatOpenAI → create_agent → 工具 → 下一轮模型请求` 代码，但没有调用真实模型、抓取在线新闻或向外推送。以下字符变化不能解释为真实 Token、费用或编辑质量变化。

## 交付物

| 内容 | 文件 |
| --- | --- |
| 预筛选规则和共用历史匹配 | [app/prefilter.py](../app/prefilter.py) |
| 接入来源工具、保留材料归档和提交兜底 | [app/tools.py](../app/tools.py) |
| 上下文整理保留预筛选统计回执 | [app/working_context.py](../app/working_context.py) |
| 默认开关 | [app/config.py](../app/config.py)、[.env.example](../.env.example) |
| 固定来源、身份偏好、日期和历史 | [tests/fixtures/prefilter.json](../tests/fixtures/prefilter.json) |
| 22 项新增检查 | [tests/test_prefilter.py](../tests/test_prefilter.py) |
| 四组开关组合的可复现验收脚本 | [scripts/prefilter_acceptance.py](../scripts/prefilter_acceptance.py) |
| 对照数据、逐条决策和完整运行材料归档 | [prefilter-proof.json](prefilter-proof.json) |
| SDK HTTP 请求中的完整消息，按组合及轮次排列 | [prefilter-requests.json](prefilter-requests.json) |

## 六项标准的结果

| 验收标准 | 实际结果与证据 | 结论 |
| --- | --- | --- |
| 1. 规范化 URL 去重，重复抓取不损坏材料 | 去除追踪参数、片段、主机名大小写和末尾斜线差异，保留有意义的查询参数。跨来源重复和再次抓取不新增候选，不覆盖已读正文、首次发布时间、阅读状态、`selected` / `excluded` 判断和证据。`test_cross_source_and_repeat_fetch_preserve_verified_material_and_decisions` 分别验证两种状态，也保留同一 ID 不同批次的原始摘要。 | 通过 |
| 2. 历史去重提前执行，范围保持一致 | 同用户、同模式、近七天的历史按 ID、规范化 URL、规范化标题拦截。仅存在于其他用户、demo 模式或八天前简报中的文章均保留。首次来源输入中的三个历史重复项不会进入候选。`test_first_pass_exact_candidates_counts_history_scope_and_full_audit` 检查精确集合和历史范围。 | 通过 |
| 3. 仅执行可确定的提前排除 | 标题按排除词字面匹配，英文词检查边界、特殊字符转义。正文中仅提到排除词、没有关注词的条目、中文偏好对应的英文条目均保留；本轮标题相同但 URL 不同也保留。九组标题匹配边界检查及精确集合检查通过。语义相关性和跨媒体事件归并继续交给模型。 | 通过 |
| 4. 数量、原因和原始来源条目可审计 | 每批记录 `raw_count`、`kept_count`、`filtered_count`、原因计数，并保存每个原始条目及对应去留原因、命中词或重复对象。固定运行共四批、25 个条目实例，均有对应原始记录和决策。完整服务器归档嵌入 `prefilter-proof.json` 各组合的 `material_archive`，临时数据库清理后仍可检查。 | 通过 |
| 5. 被过滤长文本不进入首次及后续模型材料，最终校验保留 | 捕获四组组合共 36 次 SDK HTTP 请求。预筛选开启的两组中，七个被过滤摘要的独有标记在所有轮次均不存在；工作清单只包含允许的候选。关闭预筛选的对照组能观察到同一批标记，证明检查不是空测。关闭预筛选后最终提交仍拒绝历史 ID / URL / 标题重复、同 URL 多条和排除词；原有过期文章校验继续通过。 | 通过 |
| 6. 相同冻结输入可开关对照，现有测试通过 | 四组使用同一份冻结来源、历史、时钟和工具响应计划，唯一变化是两个开关。精确候选 ID、原因计数、消息全文和字符数均已保存。原有 64 项加新增 22 项，共 86 项测试通过；Ruff 检查与全量格式检查通过。 | 通过 |

额外防误删检查：

- **中文订阅、英文新闻**：`cross-en` 在三个来源的首次抓取和后续重复抓取后仍保留，最终提交成功。来源文本没有订阅关注词的中文原样表达。
- **arXiv 较早首次提交、当天公告**：`arxiv-announced` 的首次提交为 `2026-10-01T12:00:00+00:00`，RSS 公告为 `2026-10-20T02:00:00+00:00`，固定任务时钟为 `2026-10-20T04:00:00+00:00`。候选保留，独立测试验证最终提交成功且两类日期分别保留。
- **来源成功但全部被过滤**：返回原始数 1、保留数 0、标题排除计数 1，仍计为成功覆盖，可以提交空简报；来源全部失败时禁止提交无新闻结论的原有检查继续通过。

## 固定数据的候选与原因

首次分别获取 Hugging Face、TechCrunch、arXiv 的合成来源材料，总计 15 条：

| 来源 | 原始条目 | 保留 | 过滤 |
| --- | ---: | ---: | ---: |
| huggingface | 10 | 5 | 5 |
| techcrunch | 4 | 2 | 2 |
| arxiv | 1 | 1 | 0 |
| 合计 | 15 | 8 | 7 |

保留的精确集合，按首次进入候选的顺序：

```text
cross-en
ambiguous-body
no-keyword
query-one
query-two
same-title-new-url
out-of-window
arxiv-announced
```

首次三源过滤原因：规范化 URL 重复 2、历史 ID 重复 1、历史 URL 重复 1、历史标题重复 1、明确标题排除 2。每条只记第一个确定的原因，不重复计数。

协议验收中，模型响应计划在补读并记录 `cross-en` 为入选后，又获取其他来源并重复抓取 huggingface，最后获取 arxiv 和提交。累计 25 个条目实例，开启预筛选仍只有上述 8 个候选；本次重复抓取返回 0 条，先前候选继续可用。累计过滤 17 个实例：

| 原因码 | 数量 |
| --- | ---: |
| `duplicate_id` | 5 |
| `duplicate_url` | 3 |
| `history_id` | 2 |
| `history_url` | 2 |
| `history_title` | 2 |
| `excluded_title` | 3 |

关闭预筛选时累计返回 25 个实例，候选字典按既有 ID 缓存规则保存 15 个 ID。**条目实例数、唯一候选数和最终简报条数是三个不同口径**；固定计划最终只提交 `cross-en`，并非预筛选自动选择八条全部推送。

## 实际发往模型接口的材料对照

每组 9 次模型接口请求、8 次工具调用。第一次携带抓取新闻的请求是第 3 轮。字符数为 `dumps(HTTP messages)` 的 Python 字符长度，包含角色、消息和工具调用元数据，不包含工具 schema；同组开关比较使用相同序列化方式。

| 工作上下文整理 | 预筛选 | 唯一候选 | 首次携带新闻的消息字符 | 九轮累计消息字符 |
| --- | --- | ---: | ---: | ---: |
| 开 | 关 | 15 | 8,727 | 84,861 |
| 开 | 开 | 8 | 5,878 | 60,366 |
| 关 | 关 | 15 | 7,984 | 100,998 |
| 关 | 开 | 8 | 5,176 | 59,051 |

在这份固定材料和调用计划下，已有上下文整理开启时累计减少 **28.86%** 字符；关闭上下文整理时减少 **41.53%**。这两个数只说明本样本的消息体变化，不承诺线上固定比例，不计 Token 或账单收益。真实模型可能选择不同来源、补读次数和结束时间。

可审计位置：

- `prefilter-proof.json → cases.<组合>.batches`：每批统计及逐条原因。
- `prefilter-proof.json → cases.<组合>.material_archive.prefilter_batches`：包含被过滤条目在内的完整适配器输出。
- `prefilter-proof.json → cases.<组合>.requests`：每轮字符数、被过滤标记是否出现、工作清单数量。
- `prefilter-requests.json → <组合>[轮次索引]`：实际 HTTP 消息全文；第 3 轮索引为 `2`。

本次输入文件 SHA-256：`0e250cd14197a24f21deef1c1b48bdbfe6289634220c2904f06f8e0d1201b21f`。

请求消息文件 SHA-256：`e924151ae5e8e9eda0dc11858a26ef70f6c2a601b74a8422ed3fb0bc0cb62a9c`。

## 复现与启用

在 `ai-news-agent` 项目根目录执行：

```powershell
uv run --locked --group dev python -m pytest -q
uv run --locked --group dev ruff check app tests scripts
uv run --locked --group dev ruff format --check app tests scripts
uv run --locked --group dev python -m scripts.prefilter_acceptance
```

本次最终结果：`86 passed in 14.53s`；`All checks passed!`；`31 files already formatted`。验收脚本四组执行全部成功。脚本不会读取 `.env` 的模型凭据，模型 HTTP 调用由 `httpx.MockTransport` 接管；测试数据在独立临时 SQLite 中，不修改当前用户订阅、历史或真实收件箱。

默认配置 `PREFILTER_ENABLED=true`；需要关闭时在 `.env` 设置 `PREFILTER_ENABLED=false` 并重启服务。与 `WORKING_CONTEXT_ENABLED` 独立；关闭预筛选仍保留最终提交校验。

运行时每个任务的 `data/materials/<run_id>.json` 均新增 `prefilter_batches`。模型工具回执和执行记录保留每批数量与原因计数；逐条原始材料留在服务器归档，不放回模型的文件工作区。

## 当前边界

1. 原始条目指来源适配器返回的结构化条目；原有 RSS 日期、允许域名、每源数量限制及源内去重之前的 XML 不在本次归档范围。
2. 历史仍沿用每用户最近 100 次任务中，近七天同模式已有简报的口径。按已有行为，邮件投递失败的已生成简报也在其中。本次没有改变为“仅成功投递”。
3. 标题排除是用户字面排除词的提前执行。正文中只提到排除词的内容不会提前删除；最终提交仍沿用原标题与正文的更严格排除词检查。跨语言排除词翻译、语义筛选和跨 URL 事件去重没有加入确定性规则。
4. 为验证跨来源 URL 去重，固定材料使用了两个合成来源返回同一 URL 的情况，不声称这是当前真实 RSS 适配器必然产生的输出。真实联网来源和真实模型质量应由另一次 live 验收评估，本报告不将协议模拟结果标为真实模型验收。
