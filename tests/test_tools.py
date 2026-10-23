from datetime import timedelta

import pytest
from conftest import sample_article, selection

from app.schemas import Selection
from app.tools import ToolBudgetExceeded


def test_all_required_tools_registered_and_work(toolbox):
    tools = {tool.name: tool for tool in toolbox.tools}
    assert set(tools) >= {"list_dir", "read_file", "search_content", "write_file", "bash"}
    assert "profile.json" in [p["name"] for p in tools["list_dir"].invoke({})["entries"]]
    assert tools["write_file"].invoke({"path": "scratch/notes.txt", "content": "hello\nAgent\n"})["bytes"]
    assert "Agent" in tools["read_file"].invoke({"path": "scratch/notes.txt"})["content"]
    found = tools["search_content"].invoke({"keyword": "agent", "dir": "scratch"})
    assert found["matches"][0]["line"] == 2
    shell = tools["bash"].invoke({"command": "wc -l scratch/notes.txt"})
    assert shell["returncode"] == 0, shell
    assert shell["stdout"].strip().startswith("2")
    assert len(toolbox.store.traces(toolbox.run["id"])) == 5


@pytest.mark.parametrize("path", ["../outside", "../../.env", "/etc/passwd", "C:/Windows/win.ini"])
def test_paths_cannot_escape(toolbox, path):
    with pytest.raises(ValueError):
        toolbox.read_file(path)


def test_cannot_overwrite_profile(toolbox):
    with pytest.raises(ValueError):
        toolbox.write_file("profile.json", "{}")


@pytest.mark.parametrize(
    "command",
    [
        "cat ../../.env",
        "ls; whoami",
        "echo $(env)",
        "curl https://example.com",
        "bash -c pwd",
        "cat < /etc/passwd",
        "ls -R",
        "cat scratch/../..",
    ],
)
def test_shell_rejects_unapproved_commands(toolbox, command):
    with pytest.raises((ValueError, IsADirectoryError, PermissionError)):
        toolbox.bash(command)


def test_unknown_article_and_all_failed_sources_cannot_be_published(toolbox):
    with pytest.raises(ValueError, match="成功获取"):
        toolbox.submit_brief("测试简报", "没有任何可靠来源", [])
    toolbox.successful_sources.add("huggingface")
    with pytest.raises(ValueError, match="未知文章"):
        toolbox.submit_brief("测试简报", "测试内容概述说明", [Selection(**selection("fake"))])


def test_source_fields_are_copied_and_duplicates_rejected(toolbox):
    toolbox.successful_sources.add("huggingface")
    toolbox.candidates["a1"] = sample_article()
    item = Selection(**selection())
    assert toolbox.submit_brief("测试简报", "测试内容概述说明", [item])["accepted"]
    published = toolbox.accepted["items"][0]
    assert published["url"] == toolbox.candidates["a1"]["url"]
    with pytest.raises(ValueError, match="重复"):
        toolbox.submit_brief("测试简报", "测试内容概述说明", [item, item])
    toolbox.history = [toolbox.accepted]
    with pytest.raises(ValueError, match="近期已推送"):
        toolbox.submit_brief("测试简报", "测试内容概述说明", [item])


def test_old_and_excluded_items_rejected(toolbox):
    toolbox.successful_sources.add("huggingface")
    toolbox.candidates["a1"] = sample_article()
    toolbox.candidates["a1"]["published_at"] = (toolbox.now - timedelta(days=2)).isoformat()
    with pytest.raises(ValueError, match="24 小时"):
        toolbox.submit_brief("测试简报", "测试内容概述说明", [Selection(**selection())])
    toolbox.candidates["a1"] = sample_article(title="公司融资消息")
    with pytest.raises(ValueError, match="排除"):
        toolbox.submit_brief("测试简报", "测试内容概述说明", [Selection(**selection())])


def test_tool_budget_stops_loop(toolbox):
    toolbox.settings.max_tool_calls = 1
    toolbox.tools[0].invoke({})
    with pytest.raises(ToolBudgetExceeded):
        toolbox.tools[0].invoke({})


def test_large_trace_arguments_still_valid_json(toolbox):
    tool = next(t for t in toolbox.tools if t.name == "write_file")
    tool.invoke({"path": "scratch/long.txt", "content": "x" * 30000})
    assert "truncated_preview" in toolbox.store.traces(toolbox.run["id"])[0]["arguments"]


async def test_feed_failure_empty_feed_and_partial_coverage_are_distinct(toolbox):
    toolbox.run["mode"] = "live"

    class Client:
        async def fetch(self, source_id):
            if source_id == "openai":
                raise ConnectionError("source unreachable")
            return []

    toolbox.client = Client()
    tools = {tool.name: tool for tool in toolbox.tools}
    failure = await tools["fetch_news"].ainvoke({"source_id": "openai"})
    assert "error" in failure
    rejected = tools["submit_brief"].invoke(
        {"title": "测试简报", "overview": "没有匹配的新新闻。", "selections": []}
    )
    assert "error" in rejected and toolbox.accepted is None
    success = await tools["fetch_news"].ainvoke({"source_id": "huggingface"})
    assert success["count"] == 0 and "error" not in success
    accepted = tools["submit_brief"].invoke(
        {"title": "测试简报", "overview": "可用来源中没有匹配的新新闻。", "selections": []}
    )
    assert accepted["accepted"]
    assert toolbox.accepted["sources_checked"] == ["huggingface"]
    assert "OpenAI" in toolbox.accepted["warnings"][0]
