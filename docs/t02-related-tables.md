# T02：补齐生成任务、工具记录和通知表

本轮交付：通过迁移 `0002` 建立 `runs`、`traces` 和 `notifications`，让 PostgreSQL 能保存完整的订阅关联数据，并由数据库执行必要的业务约束。

## 四张表如何关联

```text
users：一份订阅
  └─ runs：这份订阅的多次生成任务
       ├─ traces：每次生成中的多条工具调用记录
       └─ notifications：这次生成对应的最多一条站内通知
```

这里的 `users` 仍表示订阅配置档案，不是新增了登录账号体系。

| 表 | 保存内容 | 与其他表的关系 |
|---|---|---|
| `users` | 订阅编号、完整偏好、创建时间 | 一份订阅可以生成多次 |
| `runs` | 任务状态、触发方式、业务日期、简报、推送状态和错误 | `user_id` 必须对应真实订阅 |
| `traces` | 工具名、参数、返回文本、状态、耗时和时间 | `run_id` 必须对应真实任务 |
| `notifications` | 收件订阅、任务、标题和通知时间 | 订阅编号与任务编号必须成对匹配任务归属 |

## 外键解决什么问题

`runs.user_id` 引用 `users.id`。这条外键要求：先存在订阅，才能建立属于它的任务。

例如，订阅 `A` 存在时，可以为它创建任务 `R1`；不存在订阅 `B` 时，数据库会拒绝为 `B` 插入任务。

工具记录也一样：不存在任务 `R1`，就不能保存属于 `R1` 的工具记录。

通知比普通外键多一个要求：`run_id` 和 `user_id` 要一起匹配同一条任务。任务 `R1` 属于订阅 `A` 时，不能把 `(R1, B)` 写成通知，即使订阅 `B` 本身也存在。

这通过指向 `runs(id, user_id)` 的复合外键实现。`runs` 上配套的 `(id, user_id)` 唯一约束为这组引用提供唯一目标。

有历史关联的订阅或任务也不能直接删除。本轮没有配置级联删除，避免删除父记录时顺带清除历史。

## 两条任务去重规则

### 同一订阅同时只有一个活动任务

`one_active_run` 是部分唯一索引：只对状态为 `queued` 或 `running` 的任务限制订阅编号唯一。

效果是：

- 已有排队或运行任务时，同一订阅的新活动任务会被数据库拒绝。
- 原任务完成后，可以再创建新的手动任务。
- 两条独立连接并发插入时，仍然只允许一条成功。

本轮已经实际验证排队/排队、排队/运行、运行/运行三种并发组合。

### 同一订阅同一天只创建一次定时任务

`(user_id, scheduled_date)` 是唯一约束。即使当天任务已经完成，相同订阅、相同业务日期也不能再插入一条定时任务。

这里延续现有业务口径：业务日期由调度器按订阅时区计算。手动任务的日期为空，因此可以在完成上一份任务后再次手动生成；活动任务限制仍然生效。

不同订阅可以在同一天生成，同一订阅也可以在不同日期生成。

## 通知为什么不会重复

`notifications.run_id` 唯一，一次生成任务最多有一条站内通知。后续实现推送重试时，可以复用这条唯一规则处理重复写入。

这条规则约束的是站内通知记录；邮件发送的结果未知状态仍按现有业务处理。

## 数据类型与索引

- `runs.brief` 和 `traces.arguments` 使用 JSONB，便于保存和读取结构化数据。
- 未生成简报时，`brief=None` 保存为 SQL 的空值，便于按是否生成查询。
- `traces.result` 保留文本，因为既有执行记录可能保存截断后的 JSON 文本，不能要求它一定是完整 JSON。
- 调度日期使用 `date`，执行时间使用带时区时间。
- 工具记录和通知编号使用数据库生成的递增整数；保留显式写入编号的能力，为历史数据导入准备。
- 历史任务、工具记录、通知分别增加与实际查询条件对应的索引。当前没有对吞吐提升作性能结论，仍需要后续数据量与查询计划验证。

## 查看本地结果

```powershell
cd D:\Develop\CODES\Daily-AI-News-Assistant\ai-news-agent
$env:DATABASE_URL = 'postgresql+psycopg://ai_news:ai_news_dev@127.0.0.1:5432/ai_news'
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic check
docker exec ai-news-postgres psql -U ai_news -d ai_news -c '\d runs'
```

预期版本为 `0002 (head)`，结构检查返回 `No new upgrade operations detected.`。

主要文件：

- `app/db_schema.py`：四张正式表及约束的目标定义。
- `migrations/versions/0002_create_run_tables.py`：从 `0001` 建立三张关联表的步骤。
- `tests/test_postgres_schema.py`：关联、去重、并发、数据读写和迁移回退验收。

本次迁移的回退会删除新建的三张表及其数据。回退验证在独立临时数据库执行，已经验证可保留订阅表和独立练习表，再重新升级。

## 当前交付边界

这一轮完成关联表、约束和真实数据库验证。本地数据库已经具备完整业务所需的四张正式表。

后续的 PostgreSQL 完整读写方法与网页、生成服务接入现已完成，见 [完整业务接入说明](t02-postgres-application.md)。用户在 2026-10-26 明确取消 SQLite 旧数据导入，旧文件保留，T02 按调整后的范围收尾。

## 待独立练习

参照并发验收中“任务完成后可以再生成”的检查，独立增加一个测试：任务状态变为 `failed` 后，同一订阅也能新建一条手动任务，且原来的失败任务仍作为历史保留。先写下预期，再修改测试，暂不给实现。

参考：[PostgreSQL 约束](https://www.postgresql.org/docs/current/ddl-constraints.html)、[部分索引](https://www.postgresql.org/docs/current/indexes-partial.html)。
