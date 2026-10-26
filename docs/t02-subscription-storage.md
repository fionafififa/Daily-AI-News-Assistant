# T02：正式订阅的保存与查询

本轮目标：把上一轮脚本中的数据库操作整理成可以复用的正式订阅读写模块，并通过 PostgreSQL 验证新建、查询、修改和列表。

## 本轮交付与执行边界

本轮的数据路径是：

```text
验收脚本 / 测试
  → PostgresSubscriptions
  → SQLAlchemy 数据库连接
  → PostgreSQL 的 users 表
```

本页最初以独立订阅模块讲解读写。后续四张表的完整存储已经统一接入网页和生成服务，见 [完整业务接入说明](t02-postgres-application.md)。用户在 2026-10-26 明确取消 SQLite 旧数据导入，旧文件保留，T02 按调整后的范围收尾。

新增文件及职责：

| 文件 | 职责 |
|---|---|
| `app/database.py` | 从环境变量或 `.env` 取得连接地址，创建数据库连接管理对象 |
| `app/postgres_subscriptions.py` | 保存、查询、修改和列出正式订阅 |
| `scripts/check_postgres_subscriptions.py` | 实际保存一条订阅，再查询并核对内容 |
| `tests/test_postgres_subscriptions.py` | 在独立临时 PostgreSQL 数据库验证成功与失败场景 |

迁移环境 `migrations/env.py` 也复用新的连接地址读取函数。没有新增第三方依赖。

## 先运行

启动 Docker Desktop，等待引擎就绪。在项目目录运行：

```powershell
cd D:\Develop\CODES\Daily-AI-News-Assistant\ai-news-agent
docker start ai-news-postgres
$env:DATABASE_URL = 'postgresql+psycopg://ai_news:ai_news_dev@127.0.0.1:5432/ai_news'
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -X utf8 -m scripts.check_postgres_subscriptions
```

预期先输出一个新订阅编号，然后输出包含 `name`、`topics`、推送设置等内容的完整订阅。

每次运行新增一条名为“T02 正式订阅验证”的订阅，并设置 `enabled=False`。示例会保留这条记录，方便从数据库中检查。

## 一条订阅怎样保存

调用代码：

```python
profile = Subscription(name="T02 正式订阅验证", topics=["Agent"], enabled=False).model_dump()
saved = subscriptions.save_user(profile)
loaded = subscriptions.user(saved["id"])
```

执行顺序如下：

1. `Subscription(...).model_dump()` 取得完整偏好字典，包含默认字段。
2. `save_user()` 再用同一字段模型校验输入，防止独立脚本绕过接口的校验。
3. `uuid4().hex` 生成稳定的文本订阅编号，`datetime.now(UTC)` 记录创建时间。
4. `insert(users).values(...)` 构造一条插入语句。这时只是构造 Python 对象，还没有向数据库发送插入请求。
5. `connection.execute(...)` 把语句和参数交给 PostgreSQL 执行。
6. `.returning(users.c.id, users.c.profile)` 要求数据库返回本次真正写入的编号和偏好。
7. 正常离开 `engine.begin()` 代码块时提交事务；提交成功后函数才返回保存结果。
8. `user(saved["id"])` 重新取得连接，根据编号查询已提交的记录。

SQLAlchemy 负责绑定参数，名字里含中文或单引号时也按数据传递，不拼接到 SQL 字符串中。

几个首次出现的写法：`users` 是表定义，`users.c.id` 表示该表的 `id` 列；`insert()`、`select()`、`update()` 分别构造插入、查询和修改语句；`values()` 指定要写入的数据，`where()` 指定匹配条件。

## Python 字典与数据库记录的关系

调用方传入：

```python
{"name": "我的简报", "topics": ["Agent"], ...}
```

数据库中的一行包含：

```text
id          → 订阅编号
profile     → 包含名称、话题、关键词等字段的 JSONB 数据
created_at  → 带时区的创建时间
```

`profile` 字段在数据库中以 JSONB 保存，查询回 Python 后是字典，不需要像 SQLite 的文本字段那样手动 `json.loads()`。

对外返回时，`decode_user()` 把编号和偏好展开为：

```python
{"name": "我的简报", "topics": ["Agent"], ..., "id": "订阅编号"}
```

这样与原来页面使用的订阅形状保持一致。数据库的主键字段是最终编号来源。

## 四种调用行为

| 调用 | 成功结果 | 查不到或失败 |
|---|---|---|
| `save_user(profile)` | 新增订阅，返回保存后的完整订阅 | 无效偏好抛出字段校验错误；数据库错误向调用方传播 |
| `user(user_id)` | 返回一份完整订阅 | 不存在返回 `None`；数据库错误向调用方传播 |
| `save_user(profile, user_id)` | 替换该订阅的完整偏好，编号和创建时间保留 | 不存在抛出 `KeyError`，不会创建新记录 |
| `users()` | 按创建时间和编号稳定排序，返回列表 | 没有订阅返回 `[]`；数据库错误向调用方传播 |

修改偏好沿用原有 PUT 的完整替换语义；省略的字段会使用 `Subscription` 默认值。它不是只修改个别字段的 PATCH 接口。

入参 `profile` 只包含订阅偏好，不包含内部字段 `id`。修改时编号通过单独的 `user_id` 参数传入；不要把含 `id` 的完整返回值直接当作 `profile`。

“没有查到”与“查询过程出错”是两种不同结果。数据库连接失败时抛出的异常不会被转换成空列表或 `None`。

## 事务何时生效

核心结构：

```python
with self.engine.begin() as connection:
    result = connection.execute(statement.returning(users.c.id, users.c.profile))
    row = result.mappings().one_or_none()
    if row is None:
        raise KeyError(user_id)
    saved = decode_user(row)

return saved
```

- `engine` 管理数据库连接。模块接收外部传入的 `engine`，这样运行脚本和临时数据库测试可以使用相同的读写代码。
- `engine.begin()` 取得连接并管理一个事务。代码块成功结束时提交，发生异常时回滚，并继续向外抛异常。
- `mappings()` 让结果可按字段名访问，例如 `row["profile"]`。
- `one_or_none()` 返回一条记录或 `None`。同一个主键最多匹配一条记录。
- `return saved` 在代码块外，保证向调用方报告成功前已经完成提交。
- 脚本退出时由创建 `engine` 的调用方执行 `engine.dispose()`，释放连接池资源。

本轮沿用现有 `Store` 的同步函数形式，先集中理解数据读写和事务。完整业务存储切换时会一并核对异步调用边界。

建表交给 Alembic，保存订阅的方法只负责数据操作，不会自动创建或修改表结构。

## 怎样验证

普通业务回归：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -W error::RuntimeWarning
```

真实 PostgreSQL 验收需要显式设置测试连接地址。测试账号需要创建临时数据库的权限；本地 Docker 初始化的账号具备该权限。

```powershell
$env:TEST_POSTGRES_URL = 'postgresql+psycopg://ai_news:ai_news_dev@127.0.0.1:5432/ai_news'
.\.venv\Scripts\python.exe -m pytest tests/test_postgres_subscriptions.py -q -W error::RuntimeWarning
```

每个用例创建名字以 `t02_subscriptions_test_` 开头的临时数据库，执行真实 Alembic 迁移，检查结束后删除该临时数据库。测试不会往现有业务订阅表插入测试数据。

第一个测试可以按下面三部分阅读：

1. **准备**：连接已迁移好的临时数据库，准备一个含中文、单引号和重复话题的订阅。
2. **执行**：调用 `save_user()`，再调用 `user()`。
3. **断言**：确认返回内容正确、话题去重，并用另一个数据库连接管理对象读取同一记录，验证数据已经提交。

其他用例覆盖：更新后编号与创建时间不变、其他订阅不受影响、未知编号、无效偏好、重复主键回滚与后续恢复，以及列表排序。

## 待独立练习

在验收脚本中新建订阅后，将它的话题改为 `RAG`，通过 `save_user(profile, user_id)` 保存，再按编号查询。

验收要求：话题变为 `RAG`，订阅编号不变，这次修改没有额外新增另一份订阅。先自行调整调用代码，再提交审查。

参考：[SQLAlchemy 事务与结果读取](https://docs.sqlalchemy.org/en/21/tutorial/dbapi_transactions.html)、[INSERT 与 RETURNING](https://docs.sqlalchemy.org/en/21/tutorial/data_insert.html#insert-returning)。
