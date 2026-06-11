import yaml

from app.agent.guard import guard_plan_change
from app.agent.normalizer import normalize_plan_payload
from app.agent.planner import fallback_plan
from app.compiler.dify import DifyDslCompiler
from app.dify.graph import decompile_dify_graph
from app.models import WorkflowPlan
from app.validator import validate_plan


def _compiler() -> DifyDslCompiler:
    return DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="langgenius/tongyi/tongyi",
        default_model_name="qwen3.5-plus",
    )


def test_webhook_selection_replaces_start_and_registers_declared_variables() -> None:
    normalized = normalize_plan_payload(
        fallback_plan("处理售后请求").model_dump(),
        trigger_selection={
            "type": "webhook",
            "method": "POST",
            "content_type": "application/json",
            "headers": [{"name": "x-request-id", "type": "string"}],
            "params": [{"name": "urgent", "type": "boolean"}],
            "body": [{"name": "query", "type": "string", "required": True}],
            "status_code": 202,
            "response_body": '{"accepted":true}',
            "timeout": 20,
        },
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    trigger = next(node for node in plan.nodes if node.type == "trigger-webhook")

    assert not any(node.type == "start" for node in plan.nodes)
    assert trigger.params["method"] == "POST"
    assert [item["variable"] for item in trigger.params["variables"]] == [
        "_webhook_raw",
        "x_request_id",
        "urgent",
        "query",
    ]
    assert not [issue for issue in validate_plan(plan) if issue.severity == "error"]

    graph = yaml.safe_load(_compiler().compile(plan))["workflow"]["graph"]
    graph_trigger = next(node for node in graph["nodes"] if node["data"]["type"] == "trigger-webhook")
    assert graph_trigger["data"]["body"] == [{"name": "query", "type": "string", "required": True}]
    assert graph_trigger["data"]["status_code"] == 202


def test_schedule_selection_compiles_visual_configuration() -> None:
    payload = fallback_plan("每天汇总售后数据").model_dump()
    llm = next(node for node in payload["nodes"] if node["type"] == "llm")
    llm["params"]["user_prompt"] = "请生成今天的售后数据汇总。"
    normalized = normalize_plan_payload(
        payload,
        trigger_selection={
            "type": "schedule",
            "mode": "visual",
            "frequency": "daily",
            "visual_config": {
                "time": "09:00 AM",
                "weekdays": ["mon"],
                "on_minute": 0,
                "monthly_days": [1],
            },
            "timezone": "Asia/Shanghai",
        },
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    trigger = next(node for node in plan.nodes if node.type == "trigger-schedule")
    graph = yaml.safe_load(_compiler().compile(plan))["workflow"]["graph"]
    graph_trigger = next(node for node in graph["nodes"] if node["data"]["type"] == "trigger-schedule")

    assert trigger.params["timezone"] == "Asia/Shanghai"
    assert graph_trigger["data"]["frequency"] == "daily"
    assert graph_trigger["data"]["visual_config"]["time"] == "09:00 AM"
    assert not [issue for issue in validate_plan(plan) if issue.severity == "error"]


def test_schedule_time_reference_is_rewritten_to_system_timestamp() -> None:
    payload = fallback_plan("每天生成售后复盘检查清单").model_dump()
    start = next(node for node in payload["nodes"] if node["type"] == "start")
    start["id"] = "trigger_schedule_1"
    next(edge for edge in payload["edges"] if edge["source"] == "start")["source"] = (
        "trigger_schedule_1"
    )
    llm = next(node for node in payload["nodes"] if node["type"] == "llm")
    llm["params"]["user_prompt"] = (
        "今天是{{#trigger_schedule_1.time#}}，请生成今天的售后复盘检查清单。"
    )
    normalized = normalize_plan_payload(
        payload,
        trigger_selection={
            "type": "schedule",
            "mode": "visual",
            "frequency": "daily",
            "visual_config": {"time": "09:00 AM"},
            "timezone": "Asia/Shanghai",
        },
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    normalized_llm = next(node for node in plan.nodes if node.type == "llm")
    formatter = next(node for node in plan.nodes if node.type == "code")

    assert normalized_llm.params["user_prompt"] == (
        f"今天是{{{{#{formatter.id}.date#}}}}，请生成今天的售后复盘检查清单。"
    )
    assert any("schedule time reference(s)" in change for change in normalized.changes)
    assert formatter.title == "格式化计划执行时间"
    assert formatter.params["variables"][0]["value_selector"] == ["sys", "timestamp"]
    assert set(formatter.params["outputs"]) == {"date", "datetime", "weekday"}
    assert {(edge.source, edge.target) for edge in plan.edges} == {
        ("trigger_schedule_1", formatter.id),
        (formatter.id, "llm"),
        ("llm", "end"),
    }
    assert not [issue for issue in validate_plan(plan) if issue.severity == "error"]

    graph = yaml.safe_load(_compiler().compile(plan))["workflow"]["graph"]
    graph_nodes = {node["id"]: node for node in graph["nodes"]}
    graph_llm = next(node for node in graph["nodes"] if node["data"]["type"] == "llm")
    user_prompt = next(
        item for item in graph_llm["data"]["prompt_template"] if item["role"] == "user"
    )
    assert user_prompt["text"] == (
        f"今天是{{{{#{formatter.id}.date#}}}}，请生成今天的售后复盘检查清单。"
    )
    assert graph_nodes["trigger_schedule_1"]["position"]["x"] < graph_nodes[formatter.id]["position"]["x"]
    assert graph_nodes[formatter.id]["position"]["x"] < graph_nodes["llm"]["position"]["x"]
    assert graph_nodes[formatter.id]["data"]["variables"][0]["value_type"] == "number"


def test_cron_schedule_omits_visual_configuration() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "工作日复盘",
            "nodes": [
                {
                    "id": "schedule",
                    "type": "trigger-schedule",
                    "params": {
                        "mode": "cron",
                        "cron_expression": "0 18 * * 1-5",
                        "frequency": "daily",
                        "visual_config": {"time": "09:00 AM"},
                        "timezone": "Asia/Shanghai",
                    },
                },
                {
                    "id": "end",
                    "type": "end",
                    "params": {
                        "outputs": [{"variable": "answer", "value_selector": ["sys", "timestamp"]}]
                    },
                },
            ],
            "edges": [{"source": "schedule", "target": "end"}],
        }
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    schedule = next(node for node in plan.nodes if node.type == "trigger-schedule")

    assert schedule.params == {
        "mode": "cron",
        "cron_expression": "0 18 * * 1-5",
        "timezone": "Asia/Shanghai",
    }

    graph = yaml.safe_load(_compiler().compile(plan))["workflow"]["graph"]
    graph_schedule = next(
        node for node in graph["nodes"] if node["data"]["type"] == "trigger-schedule"
    )
    assert graph_schedule["data"]["cron_expression"] == "0 18 * * 1-5"
    assert "frequency" not in graph_schedule["data"]
    assert "visual_config" not in graph_schedule["data"]


def test_existing_schedule_formatter_keeps_timestamp_as_number() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "定时复盘",
            "nodes": [
                {
                    "id": "schedule",
                    "type": "trigger-schedule",
                    "params": {
                        "mode": "cron",
                        "cron_expression": "0 18 * * 1-5",
                        "timezone": "Asia/Shanghai",
                    },
                },
                {
                    "id": "format_time",
                    "type": "code",
                    "params": {
                        "code": "def main(timestamp: int) -> dict:\n    return {'date': str(timestamp)}\n",
                        "variables": [
                            {
                                "variable": "timestamp",
                                "value_selector": ["sys", "timestamp"],
                                "value_type": "string",
                            }
                        ],
                        "outputs": {"date": {"type": "string", "children": None}},
                    },
                },
                {
                    "id": "end",
                    "type": "end",
                    "params": {
                        "outputs": [{"variable": "answer", "value_selector": ["format_time", "date"]}]
                    },
                },
            ],
            "edges": [
                {"source": "schedule", "target": "format_time"},
                {"source": "format_time", "target": "end"},
            ],
        }
    )

    formatter = next(
        node for node in normalized.payload["nodes"] if node["id"] == "format_time"
    )
    assert formatter["params"]["variables"][0]["value_type"] == "number"


def test_malformed_existing_schedule_formatter_is_repaired() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "每日售后值班提醒",
            "nodes": [
                {
                    "id": "schedule",
                    "type": "trigger-schedule",
                    "params": {
                        "mode": "visual",
                        "frequency": "daily",
                        "visual_config": {"time": "12:00 PM"},
                        "timezone": "Asia/Shanghai",
                    },
                },
                {
                    "id": "format_time",
                    "type": "code",
                    "params": {
                        "code": (
                            "import datetime\n"
                            "date = datetime.datetime.now().strftime('%Y-%m-%d')\n"
                        ),
                        "variables": [
                            {
                                "variable": "timestamp",
                                "value_selector": ["sys", "timestamp"],
                            }
                        ],
                        "outputs": {
                            "date": "string",
                            "datetime": "string",
                            "weekday": "string",
                        },
                    },
                },
                {
                    "id": "llm",
                    "type": "llm",
                    "params": {
                        "system_prompt": "你是值班助理。",
                        "user_prompt": (
                            "日期 {{#format_time.date#}}，"
                            "时间 {{#format_time.datetime#}}，"
                            "星期 {{#format_time.weekday#}}。"
                        ),
                    },
                },
                {
                    "id": "end",
                    "type": "end",
                    "params": {
                        "outputs": [
                            {
                                "variable": "answer",
                                "value_selector": ["llm", "text"],
                            }
                        ]
                    },
                },
            ],
            "edges": [
                {"source": "schedule", "target": "format_time"},
                {"source": "format_time", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        }
    )

    formatter = next(
        node for node in normalized.payload["nodes"] if node["id"] == "format_time"
    )
    assert "def main(timestamp: int) -> dict:" in formatter["params"]["code"]
    assert "return {" in formatter["params"]["code"]
    assert formatter["params"]["outputs"] == {
        "date": {"type": "string", "children": None},
        "datetime": {"type": "string", "children": None},
        "weekday": {"type": "string", "children": None},
    }
    assert any(
        "repaired malformed schedule datetime formatter format_time" in change
        for change in normalized.changes
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    assert not [issue for issue in validate_plan(plan) if issue.severity == "error"]


def test_schedule_node_does_not_accept_invented_time_output() -> None:
    plan = WorkflowPlan.model_validate(
        {
            "name": "每日检查",
            "nodes": [
                {
                    "id": "schedule",
                    "type": "trigger-schedule",
                    "params": {
                        "mode": "cron",
                        "cron_expression": "0 9 * * *",
                        "timezone": "Asia/Shanghai",
                    },
                },
                {
                    "id": "llm",
                    "type": "llm",
                    "params": {
                        "system_prompt": "生成检查清单。",
                        "user_prompt": "执行时间：{{#schedule.time#}}",
                    },
                },
                {
                    "id": "end",
                    "type": "end",
                    "params": {
                        "outputs": [{"variable": "answer", "value_selector": ["llm", "text"]}]
                    },
                },
            ],
            "edges": [
                {"source": "schedule", "target": "llm"},
                {"source": "llm", "target": "end"},
            ],
        }
    )

    issues = validate_plan(plan)

    assert any(
        issue.code == "PLAN_VARIABLE_UNKNOWN" and "schedule.time" in issue.message
        for issue in issues
    )


def test_webhook_boolean_prompt_reference_uses_jinja_binding() -> None:
    payload = fallback_plan("处理售后请求").model_dump()
    llm = next(node for node in payload["nodes"] if node["type"] == "llm")
    llm["params"]["user_prompt"] = (
        "客户诉求：{{#start.query#}}\n"
        "是否紧急：{{#start.urgent#}}"
    )
    normalized = normalize_plan_payload(
        payload,
        trigger_selection={
            "type": "webhook",
            "body": [
                {"name": "query", "type": "string", "required": True},
                {"name": "urgent", "type": "boolean"},
            ],
        },
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    graph = yaml.safe_load(_compiler().compile(plan))["workflow"]["graph"]
    graph_llm = next(node for node in graph["nodes"] if node["data"]["type"] == "llm")
    user_prompt = next(
        item for item in graph_llm["data"]["prompt_template"] if item["role"] == "user"
    )

    assert user_prompt["edition_type"] == "jinja2"
    assert "{{ start_query }}" in user_prompt["jinja2_text"]
    assert "{{ start_urgent }}" in user_prompt["jinja2_text"]
    assert graph_llm["data"]["prompt_config"]["jinja2_variables"] == [
        {"variable": "start_query", "value_selector": ["start", "query"]},
        {"variable": "start_urgent", "value_selector": ["start", "urgent"]},
    ]

    restored = decompile_dify_graph(graph, name=plan.name)
    restored_llm = next(node for node in restored.nodes if node.type == "llm")
    assert restored_llm.params["user_prompt"] == llm["params"]["user_prompt"]


def test_trigger_validation_rejects_duplicate_webhook_outputs_and_bad_timezone() -> None:
    webhook = WorkflowPlan.model_validate(
        {
            "name": "Webhook",
            "nodes": [
                {
                    "id": "entry",
                    "type": "trigger-webhook",
                    "params": {
                        "method": "POST",
                        "content_type": "application/json",
                        "headers": [{"name": "query", "type": "string"}],
                        "params": [],
                        "body": [{"name": "query", "type": "string"}],
                        "status_code": 200,
                        "response_body": "",
                        "timeout": 30,
                    },
                },
                {
                    "id": "end",
                    "type": "end",
                    "params": {"outputs": [{"variable": "answer", "value_selector": ["entry", "query"]}]},
                },
            ],
            "edges": [{"source": "entry", "target": "end"}],
        }
    )
    schedule = WorkflowPlan.model_validate(
        {
            "name": "Schedule",
            "nodes": [
                {
                    "id": "entry",
                    "type": "trigger-schedule",
                    "params": {
                        "mode": "cron",
                        "cron_expression": "0 9 * * *",
                        "timezone": "Mars/Olympus",
                    },
                },
                {
                    "id": "end",
                    "type": "end",
                    "params": {"outputs": [{"variable": "answer", "value": "done"}]},
                },
            ],
            "edges": [{"source": "entry", "target": "end"}],
        }
    )

    assert any(issue.code == "PLAN_WEBHOOK_PARAMETER_DUPLICATE" for issue in validate_plan(webhook))
    assert any(issue.code == "PLAN_SCHEDULE_TIMEZONE_INVALID" for issue in validate_plan(schedule))


def test_replacing_start_with_trigger_is_guarded_as_destructive() -> None:
    before = fallback_plan("处理售后请求")
    after = WorkflowPlan.model_validate(
        normalize_plan_payload(
            before.model_dump(),
            trigger_selection={
                "type": "webhook",
                "body": [{"name": "query", "type": "string", "required": True}],
            },
        ).payload
    )
    changes = [
        {
            "type": "node_updated",
            "target": "start",
            "field": "type",
            "before": "start",
            "after": "trigger-webhook",
        }
    ]

    guard = guard_plan_change(before, after, changes)

    assert guard.ok is False
    assert any(issue.code == "PLAN_CHANGE_START_CHANGED" for issue in guard.issues)


def test_user_input_selection_can_replace_webhook_entry() -> None:
    webhook = normalize_plan_payload(
        fallback_plan("处理售后请求").model_dump(),
        trigger_selection={
            "type": "webhook",
            "body": [{"name": "query", "type": "string", "required": True}],
        },
    )
    restored = normalize_plan_payload(
        webhook.payload,
        trigger_selection={"type": "user-input"},
    )
    plan = WorkflowPlan.model_validate(restored.payload)
    start = next(node for node in plan.nodes if node.type == "start")

    assert not any(node.type == "trigger-webhook" for node in plan.nodes)
    assert start.params["variables"][0]["name"] == "query"
    assert start.params["variables"][0]["required"] is True
