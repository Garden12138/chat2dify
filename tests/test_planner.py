import json
from uuid import UUID

import httpx
import pytest

from app.agent.planner import (
    CHATFLOW_SYSTEM_PROMPT,
    PlannerError,
    WorkflowPlanner,
    _validate_creation_resource_bindings,
    _read_streamed_chat_completion,
    fallback_plan,
)
from app.config import Settings
from app.models import WorkflowPlan
from app.tasks import TaskCancelled


def _settings(
    openai_api_key: str | None = "token",
    *,
    dataset_ids: str = "",
    planner_provider: str = "openai",
    nvidia_api_key: str | None = None,
) -> Settings:
    env = {
        "DIFY_SOURCE_DIR": "../dify",
        "DIFY_DEFAULT_MODEL_PROVIDER": "openai",
        "DIFY_DEFAULT_MODEL_NAME": "gpt-4o-mini",
        "PLANNER_DEFAULT_PROVIDER": planner_provider,
    }
    if dataset_ids:
        env["DIFY_DEFAULT_DATASET_IDS"] = dataset_ids
    if openai_api_key:
        env["OPENAI_API_KEY"] = openai_api_key
    if nvidia_api_key:
        env["NVIDIA_API_KEY"] = nvidia_api_key
    return Settings.from_env(env, validate_dify=False)


class FakePlanner(WorkflowPlanner):
    def __init__(self, responses: list[str], *, settings: Settings | None = None) -> None:
        super().__init__(settings or _settings())
        self.responses = responses
        self.last_errors: list[str] = []

    def _call_llm(
        self,
        message: str,
        *,
        app_name: str | None,
        last_error: str = "",
        tool_selections: list[dict] | None = None,
        agent_selections: list[dict] | None = None,
        app_mode: str = "workflow",
    ) -> str:
        self.last_errors.append(last_error)
        if not self.responses:
            raise PlannerError("no fake response")
        return self.responses.pop(0)


def test_planner_fallback_when_no_openai_key() -> None:
    planner = WorkflowPlanner(_settings(openai_api_key=None))

    result = planner.generate("Summarize", app_name="Fallback", dsl_version="9.9.9")

    assert result.used_fallback is True
    assert result.attempts == 0
    assert result.plan.name == "Fallback"
    assert result.metadata()["mode"] == "fallback"


def test_planner_uses_nvidia_deepseek_v4_flash_payload(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}'
            yield 'data: {"choices":[{"delta":{"content":"{\\"nodes\\":[]}"}}]}'
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def stream(self, method, url, *, json, headers):
            captured["method"] = method
            captured["url"] = url
            captured["payload"] = json
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr("app.agent.planner.httpx.Client", FakeClient)
    planner = WorkflowPlanner(
        _settings(
            openai_api_key=None,
            planner_provider="nvidia",
            nvidia_api_key="nvapi-test",
        )
    )

    content = planner._call_llm("生成售后工作流", app_name="售后", last_error="bad plan")

    assert content == '{"nodes":[]}'
    assert captured["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert captured["method"] == "POST"
    assert captured["headers"]["Authorization"] == "Bearer nvapi-test"
    assert captured["payload"]["model"] == "deepseek-ai/deepseek-v4-flash"
    assert captured["payload"]["chat_template_kwargs"] == {"thinking": False}
    assert captured["payload"]["max_tokens"] == 8192
    assert captured["payload"]["stream"] is True
    assert captured["timeout"].read == 300
    assert captured["timeout"].connect == 15
    assert captured["headers"]["Connection"] == "close"
    assert len(captured["payload"]["messages"]) == 2
    user_payload = json.loads(captured["payload"]["messages"][1]["content"])
    assert user_payload["previous_validation_error"] == "bad plan"


def test_planner_retries_transient_server_disconnect(monkeypatch) -> None:
    captured = {"posts": 0, "sleeps": []}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"{\\"nodes\\":[]}"}}]}'
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, *, timeout):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def stream(self, method, url, *, json, headers):
            captured["posts"] += 1
            if captured["posts"] == 1:
                request = httpx.Request("POST", url)
                raise httpx.RemoteProtocolError(
                    "Server disconnected without sending a response.",
                    request=request,
                )
            return FakeResponse()

    monkeypatch.setattr("app.agent.planner.httpx.Client", FakeClient)
    monkeypatch.setattr("app.agent.planner.time.sleep", captured["sleeps"].append)
    planner = WorkflowPlanner(
        _settings(
            openai_api_key=None,
            planner_provider="nvidia",
            nvidia_api_key="nvapi-test",
        )
    )

    content = planner._call_llm("生成售后工作流", app_name="售后")

    assert content == '{"nodes":[]}'
    assert captured["posts"] == 2
    assert captured["sleeps"] == [1]


def test_streamed_planner_response_honors_cancellation() -> None:
    class FakeResponse:
        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"{"}}]}'
            yield 'data: {"choices":[{"delta":{"content":"}"}}]}'

    class CancellingContext:
        calls = 0

        def raise_if_cancelled(self):
            self.calls += 1
            if self.calls > 1:
                raise TaskCancelled("cancelled")

    with pytest.raises(TaskCancelled):
        _read_streamed_chat_completion(FakeResponse(), task_context=CancellingContext())


def test_fallback_plan_uses_semantic_titles_and_split_prompts() -> None:
    plan = fallback_plan("理发售后服务工作流")
    titles = [node.title for node in plan.nodes]
    llm = next(node for node in plan.nodes if node.type == "llm")

    assert titles == ["接收理发售后服务诉求", "生成理发售后服务回复", "返回理发售后服务结果"]
    assert "你是理发售后服务专员" in llm.params["system_prompt"]
    assert "{{#start.query#}}" in llm.params["user_prompt"]
    assert "审核标准" in llm.params["system_prompt"]
    assert "审核标准" not in llm.params["user_prompt"]


def test_chatflow_prompt_is_mode_specific_and_lists_certified_nodes() -> None:
    assert 'set app_mode to "advanced-chat"' in CHATFLOW_SYSTEM_PROMPT
    assert "Use exactly one start node and at least one end node." not in CHATFLOW_SYSTEM_PROMPT
    assert "then end" not in CHATFLOW_SYSTEM_PROMPT
    assert "question-classifier" in CHATFLOW_SYSTEM_PROMPT
    assert "knowledge-retrieval" in CHATFLOW_SYSTEM_PROMPT
    assert "tool, agent" in CHATFLOW_SYSTEM_PROMPT
    assert "human-input is top-level only" in CHATFLOW_SYSTEM_PROMPT
    assert "one acyclic processing chain" in CHATFLOW_SYSTEM_PROMPT
    assert "response path must finish at an answer node" in CHATFLOW_SYSTEM_PROMPT


def test_planner_success_normalizes_shorthand() -> None:
    planner = FakePlanner([json.dumps(_shorthand_plan())])

    result = planner.generate("客服分流", app_name="客服分流", dsl_version="9.9.9")

    assert result.used_fallback is False
    assert result.attempts == 1
    assert result.repaired is True
    assert result.plan.nodes[0].params["variables"][0]["name"] == "question"
    assert result.plan.edges[1].source_handle == "refund"
    assert result.plan.edges[2].source_handle == "false"


def test_planner_accepts_understanding_nodes() -> None:
    planner = FakePlanner([json.dumps(_understanding_plan())])

    result = planner.generate("修车售后分类并提取字段", app_name="修车售后服务工作流", dsl_version="9.9.9")

    assert result.used_fallback is False
    assert {node.type for node in result.plan.nodes} >= {"question-classifier", "parameter-extractor"}
    extractor = next(node for node in result.plan.nodes if node.id == "extract")
    classifier = next(node for node in result.plan.nodes if node.id == "classifier")
    assert extractor.params["parameters"][0]["name"] == "car_model"
    assert classifier.params["classes"][0]["id"] == "complaint"
    assert [edge.source_handle for edge in result.plan.edges if edge.source == "classifier"] == ["complaint", "consult"]


def test_planner_accepts_stable_builtin_nodes() -> None:
    planner = FakePlanner([json.dumps(_stable_builtin_plan())])

    result = planner.generate("维修单附件总结并筛选记录", app_name="维修单处理", dsl_version="9.9.9")

    assert result.used_fallback is False
    assert {node.type for node in result.plan.nodes} >= {
        "document-extractor",
        "variable-aggregator",
        "list-operator",
    }
    doc = next(node for node in result.plan.nodes if node.id == "doc")
    list_node = next(node for node in result.plan.nodes if node.id == "list")
    assert doc.params["variable_selector"] == ["start", "files"]
    assert list_node.params["variable"] == ["start", "items", "records"]
    assert list_node.params["limit"]["size"] == 1


def test_planner_accepts_selected_tool_node() -> None:
    planner = FakePlanner([json.dumps(_selected_tool_plan())])

    result = planner.generate(
        "调用搜索工具查询后总结",
        app_name="工具查询总结",
        dsl_version="9.9.9",
        tool_selections=[
            {
                "provider_id": "provider-1",
                "provider_type": "builtin",
                "provider_name": "websearch",
                "tool_name": "search",
                "tool_label": "搜索",
                "parameters": [{"name": "query", "form": "llm", "type": "string", "required": True}],
                "output_schema": {"properties": {"answer": {"type": "string"}}},
            }
        ],
    )

    tool = next(node for node in result.plan.nodes if node.id == "lookup")
    assert tool.type == "tool"
    assert tool.title == "调用搜索"
    assert tool.params["provider_id"] == "provider-1"
    assert tool.params["tool_parameters"]["query"] == {"type": "mixed", "value": "{{#start.query#}}"}


def test_chatflow_planner_converts_multiple_branch_ends() -> None:
    planner = FakePlanner([json.dumps(_chatflow_multi_end_plan())])

    result = planner.generate(
        "创建投诉和咨询分流 Chatflow",
        app_name="客服分流",
        app_mode="advanced-chat",
        dsl_version="9.9.9",
    )

    assert result.plan.app_mode == "advanced-chat"
    assert {node.id for node in result.plan.nodes if node.type == "answer"} == {
        "complaint_end",
        "consult_end",
    }
    assert not [node for node in result.plan.nodes if node.type == "end"]
    assert result.attempts == 1


def test_chatflow_planner_retries_tool_not_selected_in_request() -> None:
    bad = _chatflow_tool_plan("invented-provider", "invented-tool")
    good = _chatflow_tool_plan("provider-2", "lookup")
    planner = FakePlanner([json.dumps(bad), json.dumps(good)])
    selections = [
        {
            "provider_id": "provider-1",
            "provider_type": "builtin",
            "provider_name": "search-one",
            "tool_name": "search",
            "parameters": [{"name": "query", "form": "llm", "type": "string", "required": True}],
            "output_schema": {"properties": {"answer": {"type": "string"}}},
        },
        {
            "provider_id": "provider-2",
            "provider_type": "builtin",
            "provider_name": "search-two",
            "tool_name": "lookup",
            "parameters": [{"name": "query", "form": "llm", "type": "string", "required": True}],
            "output_schema": {"properties": {"answer": {"type": "string"}}},
        },
    ]

    result = planner.generate(
        "调用已选择的查询工具",
        app_name="工具客服",
        app_mode="advanced-chat",
        dsl_version="9.9.9",
        tool_selections=selections,
    )

    tool = next(node for node in result.plan.nodes if node.type == "tool")
    assert result.attempts == 2
    assert "PLAN_TOOL_NOT_SELECTED" in planner.last_errors[1]
    assert tool.params["provider_id"] == "provider-2"
    assert tool.params["tool_name"] == "lookup"
    assert tool.params["tool_parameters"]["query"]["value"] == "{{#sys.query#}}"


def test_chatflow_planner_retries_invented_dataset_id() -> None:
    bad = _chatflow_knowledge_plan(dataset_ids=["invented-dataset"])
    good = _chatflow_knowledge_plan(dataset_ids=[])
    planner = FakePlanner(
        [json.dumps(bad), json.dumps(good)],
        settings=_settings(dataset_ids="dataset-real"),
    )

    result = planner.generate(
        "根据选中的知识库回答",
        app_name="知识库客服",
        app_mode="advanced-chat",
        dsl_version="9.9.9",
    )

    knowledge = next(node for node in result.plan.nodes if node.type == "knowledge-retrieval")
    assert result.attempts == 2
    assert "PLAN_KNOWLEDGE_DATASET_NOT_SELECTED" in planner.last_errors[1]
    assert knowledge.params["dataset_ids"] == ["dataset-real"]


def test_chatflow_planner_accepts_top_level_human_input() -> None:
    planner = FakePlanner([json.dumps(_human_input_plan())])

    result = planner.generate(
        "生成回复后交给经理人工审核，再根据通过或驳回回复客户",
        app_name="售后人工审核",
        app_mode="advanced-chat",
        dsl_version="9.9.9",
    )

    review = next(node for node in result.plan.nodes if node.id == "review")
    llm = next(node for node in result.plan.nodes if node.id == "llm")

    assert result.attempts == 1
    assert review.type == "human-input"
    assert {edge.source_handle for edge in result.plan.edges if edge.source == "review"} == {
        "approve",
        "reject",
    }
    assert {node.id for node in result.plan.nodes if node.type == "answer"} == {
        "approved",
        "rejected",
    }
    assert "{{#sys.query#}}" in llm.params["user_prompt"]
    assert llm.params["memory"]["window"] == {"enabled": True, "size": 10}


def test_chatflow_planner_accepts_iteration_with_chat_memory() -> None:
    planner = FakePlanner([json.dumps(_iteration_plan())])

    result = planner.generate(
        "批量处理记录列表，逐条结合本轮要求生成建议",
        app_name="批量售后处理",
        app_mode="advanced-chat",
        dsl_version="9.9.9",
    )

    batch = next(node for node in result.plan.nodes if node.id == "batch")
    item_llm = next(
        child for child in batch.params["children"] if child["id"] == "item_llm"
    )

    assert result.attempts == 1
    assert batch.type == "iteration"
    assert batch.params["output_selector"] == ["item_llm", "text"]
    assert "{{#batch.item#}}" in item_llm["params"]["user_prompt"]
    assert "{{#sys.query#}}" in item_llm["params"]["user_prompt"]
    assert item_llm["params"]["memory"]["window"] == {"enabled": True, "size": 10}
    assert next(node for node in result.plan.nodes if node.id == "end").type == "answer"


def test_chatflow_planner_accepts_loop_and_inserts_internal_assigner() -> None:
    plan = _loop_plan()
    retry = next(node for node in plan["nodes"] if node["id"] == "retry")
    retry["params"]["break_conditions"] = [
        {
            "id": "completed",
            "variable_selector": ["retry_llm", "text"],
            "comparison_operator": "contains",
            "value": "已完成",
            "varType": "string",
        }
    ]
    planner = FakePlanner([json.dumps(plan)])

    result = planner.generate(
        "最多循环三次检查状态，检测到已完成后停止",
        app_name="维修状态检查",
        app_mode="advanced-chat",
        dsl_version="9.9.9",
    )

    retry_node = next(node for node in result.plan.nodes if node.id == "retry")
    children = retry_node.params["children"]
    retry_llm = next(child for child in children if child["id"] == "retry_llm")
    assigner = next(child for child in children if child["type"] == "assigner")
    condition_selector = retry_node.params["break_conditions"][0]["variable_selector"]

    assert result.attempts == 1
    assert condition_selector[0] == "retry"
    assert assigner["params"]["items"][0]["variable_selector"] == condition_selector
    assert assigner["params"]["items"][0]["value"] == ["retry_llm", "text"]
    assert "{{#sys.query#}}" in retry_llm["params"]["user_prompt"]
    assert retry_llm["params"]["memory"]["window"] == {"enabled": True, "size": 10}


def test_chatflow_planner_retries_forbidden_container_node() -> None:
    bad = _iteration_plan()
    batch = next(node for node in bad["nodes"] if node["id"] == "batch")
    batch["params"]["children"][1] = {
        "id": "review",
        "type": "human-input",
        "title": "逐条人工审核记录",
        "params": _human_input_plan()["nodes"][2]["params"],
    }
    batch["params"]["output_selector"] = ["review", "__action_id"]
    good = _iteration_plan()
    planner = FakePlanner([json.dumps(bad), json.dumps(good)])

    result = planner.generate(
        "批量处理记录并生成建议",
        app_name="批量售后处理",
        app_mode="advanced-chat",
        dsl_version="9.9.9",
    )

    assert result.attempts == 2
    assert "PLAN_CHATFLOW_CONTAINER_NODE_NOT_SUPPORTED" in planner.last_errors[1]
    batch_node = next(node for node in result.plan.nodes if node.id == "batch")
    assert not [
        child for child in batch_node.params["children"] if child["type"] == "human-input"
    ]


def test_chatflow_planner_retries_container_cycle() -> None:
    bad = _iteration_plan()
    batch = next(node for node in bad["nodes"] if node["id"] == "batch")
    batch["params"]["edges"].append({"source": "item_llm", "target": "batch_start"})
    good = _iteration_plan()
    planner = FakePlanner([json.dumps(bad), json.dumps(good)])

    result = planner.generate(
        "批量处理记录并生成建议",
        app_name="批量售后处理",
        app_mode="advanced-chat",
        dsl_version="9.9.9",
    )

    assert result.attempts == 2
    assert "PLAN_CHATFLOW_CONTAINER_CYCLE_INVALID" in planner.last_errors[1]


def test_chatflow_creation_recursively_rejects_unselected_container_resources() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "嵌套资源检查",
            "app_mode": "advanced-chat",
            "nodes": [
                {"id": "start", "type": "start", "params": {"variables": []}},
                {
                    "id": "batch",
                    "type": "iteration",
                    "params": {
                        "start_node_id": "batch_start",
                        "iterator_selector": ["start", "items"],
                        "output_selector": ["agent", "text"],
                        "children": [
                            {"id": "batch_start", "type": "iteration-start", "params": {}},
                            {
                                "id": "knowledge",
                                "type": "knowledge-retrieval",
                                "params": {"dataset_ids": ["invented-dataset"]},
                            },
                            {
                                "id": "tool",
                                "type": "tool",
                                "params": {
                                    "provider_id": "invented-provider",
                                    "provider_type": "builtin",
                                    "tool_name": "invented-tool",
                                },
                            },
                            {
                                "id": "agent",
                                "type": "agent",
                                "params": {
                                    "agent_strategy_provider_name": "invented-agent-provider",
                                    "agent_strategy_name": "invented-agent",
                                },
                            },
                        ],
                        "edges": [
                            {"source": "batch_start", "target": "knowledge"},
                            {"source": "knowledge", "target": "tool"},
                            {"source": "tool", "target": "agent"},
                        ],
                    },
                },
                {
                    "id": "answer",
                    "type": "answer",
                    "params": {"answer": "{{#batch.output#}}"},
                },
            ],
            "edges": [
                {"source": "start", "target": "batch"},
                {"source": "batch", "target": "answer"},
            ],
        }
    )

    issues = _validate_creation_resource_bindings(
        plan,
        dataset_ids=["dataset-real"],
        tool_selections=[
            {
                "provider_id": "provider-real",
                "provider_type": "builtin",
                "tool_name": "tool-real",
            }
        ],
        agent_selections=[
            {
                "agent_strategy_provider_name": "agent-provider-real",
                "agent_strategy_name": "agent-real",
            }
        ],
    )

    assert {
        "PLAN_KNOWLEDGE_DATASET_NOT_SELECTED",
        "PLAN_TOOL_NOT_SELECTED",
        "PLAN_AGENT_NOT_SELECTED",
    } <= {issue.code for issue in issues}
    assert all(".params.children." in str(issue.path) for issue in issues)


def test_planner_accepts_knowledge_retrieval_node_with_default_dataset_ids() -> None:
    planner = FakePlanner(
        [json.dumps(_knowledge_plan())],
        settings=_settings(dataset_ids="dataset-a,dataset-b"),
    )

    result = planner.generate("根据知识库回答修车售后政策", app_name="售后知识库问答", dsl_version="9.9.9")

    knowledge = next(node for node in result.plan.nodes if node.id == "knowledge")
    assert knowledge.type == "knowledge-retrieval"
    assert knowledge.params["dataset_ids"] == ["dataset-a", "dataset-b"]
    assert knowledge.params["multiple_retrieval_config"]["top_k"] == 4


def test_planner_accepts_human_input_node() -> None:
    planner = FakePlanner([json.dumps(_human_input_plan())])

    result = planner.generate("需要经理人工审核后再回复客户", app_name="售后人工审核", dsl_version="9.9.9")

    review = next(node for node in result.plan.nodes if node.id == "review")
    assert review.type == "human-input"
    assert str(UUID(review.params["delivery_methods"][0]["id"])) == review.params["delivery_methods"][0]["id"]
    assert review.params["delivery_methods"][0]["id"] != "webapp-1"
    assert review.params["delivery_methods"][0]["type"] == "webapp"
    assert [action["id"] for action in review.params["user_actions"]] == ["approve", "reject"]
    assert sorted(edge.source_handle for edge in result.plan.edges if edge.source == "review") == ["approve", "reject"]


def test_planner_accepts_iteration_node() -> None:
    planner = FakePlanner([json.dumps(_iteration_plan())])

    result = planner.generate("批量处理售后记录列表并逐条生成建议", app_name="批量售后处理", dsl_version="9.9.9")

    batch = next(node for node in result.plan.nodes if node.id == "batch")
    assert batch.type == "iteration"
    assert batch.params["iterator_selector"] == ["start", "items", "records"]
    assert batch.params["output_selector"] == ["item_llm", "text"]
    assert [child["type"] for child in batch.params["children"]] == ["iteration-start", "llm"]
    assert batch.params["edges"][0]["source"] == "batch_start"


def test_planner_accepts_loop_node() -> None:
    planner = FakePlanner([json.dumps(_loop_plan())])

    result = planner.generate("最多 3 次循环检查维修状态", app_name="维修状态检查", dsl_version="9.9.9")

    retry = next(node for node in result.plan.nodes if node.id == "retry")
    assert retry.type == "loop"
    assert retry.params["loop_count"] == 3
    assert [child["type"] for child in retry.params["children"]] == ["loop-start", "llm"]
    assert retry.params["edges"][0]["target"] == "retry_llm"


def test_planner_self_repairs_after_validation_failure() -> None:
    bad = {
        "name": "bad",
        "nodes": [
            {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
            {"id": "llm", "type": "llm", "params": {"user_prompt": "{{#start.missing#}}"}},
            {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
        ],
        "edges": [
            {"source": "start", "target": "llm"},
            {"source": "llm", "target": "end"},
        ],
    }
    good = {
        **bad,
        "nodes": [
            {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
            {"id": "llm", "type": "llm", "params": {"user_prompt": "{{#start.query#}}"}},
            {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
        ],
    }
    planner = FakePlanner([json.dumps(bad), json.dumps(good)])

    result = planner.generate("fix", dsl_version="9.9.9")

    assert result.attempts == 2
    assert result.repaired is True
    assert "PLAN_VARIABLE_UNKNOWN" in planner.last_errors[1]


def test_planner_fails_after_three_bad_attempts() -> None:
    planner = FakePlanner(["{}", "{}", "{}"])

    with pytest.raises(PlannerError) as exc:
        planner.generate("bad", dsl_version="9.9.9")

    assert "after 3 attempts" in str(exc.value)


def _shorthand_plan() -> dict:
    return {
        "nodes": [
            {"id": "start_1", "type": "start", "params": {"inputs": [{"variable": "question"}]}},
            {
                "id": "if_1",
                "type": "if-else",
                "params": {"cases": [{"id": "refund", "condition": "{{start_1.question}} contains \"退款\""}]},
            },
            {"id": "llm_refund", "type": "llm", "params": {"prompt": "处理 {{start_1.question}}"}},
            {"id": "llm_general", "type": "llm", "params": {"prompt": "通用 {{start_1.question}}"}},
            {"id": "end_refund", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_refund", "text"]}]}},
            {"id": "end_general", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_general", "text"]}]}},
        ],
        "edges": [
            {"source": "start_1", "target": "if_1"},
            {"source": "if_1", "target": "llm_refund"},
            {"source": "if_1", "target": "llm_general"},
            {"source": "llm_refund", "target": "end_refund"},
            {"source": "llm_general", "target": "end_general"},
        ],
    }


def _understanding_plan() -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "start", "title": "接收修车售后诉求", "params": {"variables": [{"name": "query"}]}},
            {
                "id": "extract",
                "type": "parameter-extractor",
                "title": "提取修车售后信息",
                "params": {
                    "query": ["start", "query"],
                    "parameters": [
                        {"name": "car_model", "type": "string", "description": "车辆型号", "required": False},
                        {"name": "issue", "type": "string", "description": "用户诉求", "required": True},
                    ],
                },
            },
            {
                "id": "classifier",
                "type": "question-classifier",
                "title": "识别售后类型",
                "params": {
                    "query_variable_selector": ["start", "query"],
                    "classes": [
                        {"id": "complaint", "name": "投诉"},
                        {"id": "consult", "name": "咨询"},
                    ],
                },
            },
            {"id": "llm_complaint", "type": "llm", "title": "生成投诉回复", "params": {"user_prompt": "投诉：{{#extract.issue#}}"}},
            {"id": "llm_consult", "type": "llm", "title": "生成咨询回复", "params": {"user_prompt": "咨询：{{#start.query#}}"}},
            {"id": "end_complaint", "type": "end", "title": "返回投诉结果", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_complaint", "text"]}]}},
            {"id": "end_consult", "type": "end", "title": "返回咨询结果", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm_consult", "text"]}]}},
        ],
        "edges": [
            {"source": "start", "target": "extract"},
            {"source": "extract", "target": "classifier"},
            {"source": "classifier", "target": "llm_complaint", "source_handle": "complaint"},
            {"source": "classifier", "target": "llm_consult", "source_handle": "consult"},
            {"source": "llm_complaint", "target": "end_complaint"},
            {"source": "llm_consult", "target": "end_consult"},
        ],
    }


def _stable_builtin_plan() -> dict:
    return {
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "title": "接收维修单和售后记录",
                "params": {
                    "variables": [
                        {"name": "query", "type": "paragraph"},
                        {"name": "files", "type": "file-list"},
                        {"name": "items", "type": "json"},
                    ]
                },
            },
            {"id": "doc", "type": "document-extractor", "title": "提取维修单文本", "params": {"variable_selector": ["start", "files"], "is_array_file": True}},
            {
                "id": "aggregate",
                "type": "variable-aggregator",
                "title": "聚合售后上下文",
                "params": {"variables": [["doc", "text"], ["start", "query"]], "output_type": "string"},
            },
            {
                "id": "list",
                "type": "list-operator",
                "title": "筛选投诉记录",
                "params": {
                    "variable": ["start", "items", "records"],
                    "var_type": "array[string]",
                    "item_var_type": "string",
                    "filter_by": {"enabled": True, "conditions": [{"comparison_operator": "contains", "value": "投诉"}]},
                    "limit": {"enabled": True, "size": 1},
                },
            },
            {"id": "llm", "type": "llm", "title": "生成维修单总结", "params": {"user_prompt": "{{#aggregate.output#}}\n{{#list.first_record#}}"}},
            {"id": "end", "type": "end", "title": "返回总结结果", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
        ],
        "edges": [
            {"source": "start", "target": "doc"},
            {"source": "doc", "target": "aggregate"},
            {"source": "aggregate", "target": "list"},
            {"source": "list", "target": "llm"},
            {"source": "llm", "target": "end"},
        ],
    }


def _human_input_plan() -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "start", "title": "接收售后诉求", "params": {"variables": [{"name": "query"}]}},
            {
                "id": "llm",
                "type": "llm",
                "title": "生成待审核回复",
                "params": {
                    "system_prompt": "你是售后客服专员，先生成待人工审核的回复草稿。",
                    "user_prompt": "请根据用户诉求生成回复草稿：{{#start.query#}}",
                },
            },
            {
                "id": "review",
                "type": "human-input",
                "title": "经理审核售后回复",
                "params": {
                    "delivery_methods": [{"id": "webapp-1", "type": "webapp", "enabled": True, "config": {}}],
                    "form_content": "请审核以下回复草稿：{{#llm.text#}}",
                    "inputs": [{"type": "paragraph", "output_variable_name": "review_comment", "default": {"type": "constant", "selector": [], "value": ""}}],
                    "user_actions": [
                        {"id": "approve", "title": "通过", "button_style": "primary"},
                        {"id": "reject", "title": "驳回", "button_style": "default"},
                    ],
                    "timeout": 3,
                    "timeout_unit": "day",
                },
            },
            {"id": "approved", "type": "end", "title": "返回审核通过结果", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
            {"id": "rejected", "type": "end", "title": "返回审核驳回结果", "params": {"outputs": [{"variable": "comment", "value_selector": ["review", "review_comment"]}]}},
        ],
        "edges": [
            {"source": "start", "target": "llm"},
            {"source": "llm", "target": "review"},
            {"source": "review", "target": "approved", "source_handle": "approve"},
            {"source": "review", "target": "rejected", "source_handle": "reject"},
        ],
    }


def _selected_tool_plan() -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "start", "params": {"variables": [{"name": "query"}]}},
            {
                "id": "lookup",
                "type": "tool",
                "title": "Tool",
                "params": {
                    "provider_id": "provider-1",
                    "provider_type": "builtin",
                    "tool_name": "search",
                },
            },
            {
                "id": "summarize",
                "type": "llm",
                "title": "总结工具查询结果",
                "params": {"user_prompt": "请总结工具结果：{{#lookup.answer#}}"},
            },
            {"id": "end", "type": "end", "params": {"outputs": [{"variable": "answer", "value_selector": ["summarize", "text"]}]}},
        ],
        "edges": [
            {"source": "start", "target": "lookup"},
            {"source": "lookup", "target": "summarize"},
            {"source": "summarize", "target": "end"},
        ],
    }


def _knowledge_plan() -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "start", "title": "接收售后问题", "params": {"variables": [{"name": "query"}]}},
            {
                "id": "knowledge",
                "type": "knowledge_retrieval",
                "title": "检索售后政策知识库",
                "params": {
                    "query_variable_selector": ["start", "query"],
                    "retrieval_mode": "multiple",
                    "multiple_retrieval_config": {"top_k": 4, "reranking_enable": False},
                },
            },
            {
                "id": "llm",
                "type": "llm",
                "title": "生成知识库回复",
                "params": {"user_prompt": "资料：{{#knowledge.result#}}\n问题：{{#start.query#}}"},
            },
            {"id": "end", "type": "end", "title": "返回回复", "params": {"outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]}},
        ],
        "edges": [
            {"source": "start", "target": "knowledge"},
            {"source": "knowledge", "target": "llm"},
            {"source": "llm", "target": "end"},
        ],
    }


def _iteration_plan() -> dict:
    return {
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "title": "接收售后记录列表",
                "params": {"variables": [{"name": "items", "type": "json"}]},
            },
            {
                "id": "batch",
                "type": "list_loop",
                "title": "批量处理售后记录",
                "params": {
                    "iterator_selector": ["start", "items", "records"],
                    "output_selector": ["item_llm", "text"],
                    "children": [
                        {"id": "batch_start", "type": "iteration-start", "title": "开始遍历", "params": {}},
                        {
                            "id": "item_llm",
                            "type": "llm",
                            "title": "逐条生成处理建议",
                            "params": {
                                "system_prompt": "你是售后服务专员，逐条分析售后记录并给出处理建议。",
                                "user_prompt": "请处理当前记录：{{#batch.item#}}",
                            },
                        },
                    ],
                    "edges": [{"source": "batch_start", "target": "item_llm"}],
                },
            },
            {"id": "end", "type": "end", "title": "返回建议列表", "params": {"outputs": [{"variable": "answers", "value_selector": ["batch", "output"]}]}},
        ],
        "edges": [{"source": "start", "target": "batch"}, {"source": "batch", "target": "end"}],
    }


def _loop_plan() -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "start", "title": "接收维修状态问题", "params": {"variables": [{"name": "query"}]}},
            {
                "id": "retry",
                "type": "retry_loop",
                "title": "循环检查维修状态",
                "params": {
                    "loop_count": 3,
                    "children": [
                        {"id": "retry_start", "type": "loop-start", "title": "开始循环", "params": {}},
                        {
                            "id": "retry_llm",
                            "type": "llm",
                            "title": "执行状态检查",
                            "params": {
                                "system_prompt": "你是维修状态检查专员，按规则检查是否已满足回复条件。",
                                "user_prompt": "请检查当前维修状态：{{#start.query#}}",
                            },
                        },
                    ],
                    "edges": [{"source": "retry_start", "target": "retry_llm"}],
                },
            },
            {"id": "end", "type": "end", "title": "返回检查结果", "params": {"outputs": [{"variable": "answer", "value_selector": ["start", "query"]}]}},
        ],
        "edges": [{"source": "start", "target": "retry"}, {"source": "retry", "target": "end"}],
    }


def _chatflow_multi_end_plan() -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "start", "title": "接收客户问题", "params": {"variables": []}},
            {
                "id": "classifier",
                "type": "question-classifier",
                "title": "判断客户诉求",
                "params": {
                    "query_variable_selector": ["start", "query"],
                    "classes": [
                        {"id": "complaint", "name": "投诉"},
                        {"id": "consult", "name": "咨询"},
                    ],
                    "instruction": "判断客户诉求类型。",
                },
            },
            {
                "id": "complaint_llm",
                "type": "llm",
                "title": "生成投诉回复",
                "params": {"user_prompt": "回复投诉：{{#start.query#}}"},
            },
            {
                "id": "consult_llm",
                "type": "llm",
                "title": "生成咨询回复",
                "params": {"user_prompt": "回复咨询：{{#start.query#}}"},
            },
            {
                "id": "complaint_end",
                "type": "end",
                "title": "回复投诉客户",
                "params": {"outputs": [{"variable": "answer", "value_selector": ["complaint_llm", "text"]}]},
            },
            {
                "id": "consult_end",
                "type": "end",
                "title": "回复咨询客户",
                "params": {"outputs": [{"variable": "answer", "value_selector": ["consult_llm", "text"]}]},
            },
        ],
        "edges": [
            {"source": "start", "target": "classifier"},
            {"source": "classifier", "target": "complaint_llm", "source_handle": "complaint"},
            {"source": "classifier", "target": "consult_llm", "source_handle": "consult"},
            {"source": "complaint_llm", "target": "complaint_end"},
            {"source": "consult_llm", "target": "consult_end"},
        ],
    }


def _chatflow_tool_plan(provider_id: str, tool_name: str) -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "start", "title": "接收查询问题", "params": {"variables": []}},
            {
                "id": "tool",
                "type": "tool",
                "title": "调用查询工具",
                "params": {
                    "provider_id": provider_id,
                    "provider_type": "builtin",
                    "tool_name": tool_name,
                    "tool_parameters": {
                        "query": {"type": "mixed", "value": "{{#start.query#}}"}
                    },
                },
            },
            {
                "id": "answer",
                "type": "answer",
                "title": "返回工具结果",
                "params": {"answer": "{{#tool.answer#}}"},
            },
        ],
        "edges": [
            {"source": "start", "target": "tool"},
            {"source": "tool", "target": "answer"},
        ],
    }


def _chatflow_knowledge_plan(*, dataset_ids: list[str]) -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "start", "title": "接收知识问题", "params": {"variables": []}},
            {
                "id": "knowledge",
                "type": "knowledge-retrieval",
                "title": "检索选中知识库",
                "params": {
                    "query_variable_selector": ["start", "query"],
                    "dataset_ids": dataset_ids,
                },
            },
            {
                "id": "llm",
                "type": "llm",
                "title": "生成知识回答",
                "params": {
                    "user_prompt": "问题：{{#start.query#}}\n资料：{{#knowledge.result#}}"
                },
            },
            {
                "id": "answer",
                "type": "answer",
                "title": "回复知识答案",
                "params": {"answer": "{{#llm.text#}}"},
            },
        ],
        "edges": [
            {"source": "start", "target": "knowledge"},
            {"source": "knowledge", "target": "llm"},
            {"source": "llm", "target": "answer"},
        ],
    }
