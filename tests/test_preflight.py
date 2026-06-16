from app.agent.normalizer import normalize_plan_payload
from app.agent.planner import fallback_plan
from app.compiler.dify import DifyDslCompiler
from app.dify.preflight import preflight_plan
from app.models import WorkflowPlan


def _compiler() -> DifyDslCompiler:
    return DifyDslCompiler(
        dsl_version="9.9.9",
        default_model_provider="openai",
        default_model_name="gpt-4o-mini",
    )


def test_preflight_roundtrips_basic_workflow_and_chatflow() -> None:
    for mode in ("workflow", "advanced-chat"):
        result = preflight_plan(
            fallback_plan("hello", app_mode=mode),
            compiler=_compiler(),
            expected_dsl_version="9.9.9",
        )

        assert result.ok is True
        assert result.roundtrip_ok is True
        assert result.dsl_version == "9.9.9"
        assert result.issues == []


def test_preflight_preserves_container_branches_handles_and_output() -> None:
    normalized = normalize_plan_payload(
        {
            "name": "批量分类",
            "app_mode": "advanced-chat",
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "title": "接收批量请求",
                    "params": {"variables": [{"name": "items", "type": "json"}]},
                },
                {
                    "id": "batch",
                    "type": "iteration",
                    "title": "逐条处理记录",
                    "params": {
                        "iterator_selector": ["start", "items", "records"],
                        "iterator_input_type": "array[string]",
                        "output_selector": ["merge", "output"],
                        "output_type": "array[string]",
                        "children": [
                            {
                                "id": "batch_start",
                                "type": "iteration-start",
                                "title": "遍历入口",
                                "params": {},
                            },
                            {
                                "id": "route",
                                "type": "if-else",
                                "title": "判断记录类型",
                                "params": {
                                    "cases": [
                                        {
                                            "case_id": "urgent",
                                            "logical_operator": "and",
                                            "conditions": [
                                                {
                                                    "variable_selector": [
                                                        "batch",
                                                        "item",
                                                    ],
                                                    "comparison_operator": "contains",
                                                    "value": "紧急",
                                                    "varType": "string",
                                                }
                                            ],
                                        }
                                    ]
                                },
                            },
                            {
                                "id": "urgent_llm",
                                "type": "llm",
                                "title": "生成紧急建议",
                                "params": {
                                    "system_prompt": "你是紧急售后专员。",
                                    "user_prompt": (
                                        "本轮要求：{{#sys.query#}}\n"
                                        "记录：{{#batch.item#}}"
                                    ),
                                },
                            },
                            {
                                "id": "normal_llm",
                                "type": "llm",
                                "title": "生成普通建议",
                                "params": {
                                    "system_prompt": "你是普通售后专员。",
                                    "user_prompt": (
                                        "本轮要求：{{#sys.query#}}\n"
                                        "记录：{{#batch.item#}}"
                                    ),
                                },
                            },
                            {
                                "id": "merge",
                                "type": "variable-aggregator",
                                "title": "汇合处理结果",
                                "params": {
                                    "variables": [
                                        ["urgent_llm", "text"],
                                        ["normal_llm", "text"],
                                    ],
                                    "output_type": "string",
                                },
                            },
                        ],
                        "edges": [
                            {"source": "batch_start", "target": "route"},
                            {
                                "source": "route",
                                "target": "urgent_llm",
                                "source_handle": "urgent",
                            },
                            {
                                "source": "route",
                                "target": "normal_llm",
                                "source_handle": "false",
                            },
                            {"source": "urgent_llm", "target": "merge"},
                            {"source": "normal_llm", "target": "merge"},
                        ],
                    },
                },
                {
                    "id": "answer",
                    "type": "answer",
                    "title": "回复批量结果",
                    "params": {"answer": "{{#batch.output#}}"},
                },
            ],
            "edges": [
                {"source": "start", "target": "batch"},
                {"source": "batch", "target": "answer"},
            ],
        },
        app_mode="advanced-chat",
    )
    plan = WorkflowPlan.model_validate(normalized.payload)

    result = preflight_plan(
        plan,
        compiler=_compiler(),
        expected_dsl_version="9.9.9",
    )

    assert result.ok is True
    assert result.roundtrip_ok is True


def test_preflight_reports_roundtrip_signature_drift(monkeypatch) -> None:
    plan = fallback_plan("hello")
    changed = plan.model_copy(deep=True)
    changed.nodes[1].id = "changed_llm"
    changed.edges[0].target = "changed_llm"
    changed.edges[1].source = "changed_llm"

    monkeypatch.setattr(
        "app.dify.preflight.decompile_dify_graph",
        lambda *_args, **_kwargs: changed,
    )

    result = preflight_plan(
        plan,
        compiler=_compiler(),
        expected_dsl_version="9.9.9",
    )

    assert result.ok is False
    assert result.roundtrip_ok is False
    assert any(
        issue.code == "DIFY_PREFLIGHT_ROUNDTRIP_MISMATCH"
        for issue in result.issues
    )


def test_normalizer_repairs_nim_empty_ids_placeholders_and_nested_models() -> None:
    normalized = normalize_plan_payload(
        {
            "app_name": "多模型售后分类",
            "app_mode": "advanced-chat",
            "nodes": [
                {
                    "id": "",
                    "type": "start",
                    "title": "用户输入",
                    "params": {},
                },
                {
                    "id": "",
                    "type": "question-classifier",
                    "title": "售后问题分类",
                    "params": {
                        "query_variable_selector": ["<start_id>", "sys.query"],
                        "classes": [
                            {"id": "complaint", "name": "投诉"},
                            {"id": "inquiry", "name": "咨询"},
                        ],
                        "instruction": "判断投诉或咨询。",
                    },
                },
                {
                    "id": "",
                    "type": "llm",
                    "title": "投诉处理",
                    "params": {
                        "model": {
                            "provider": "langgenius/tongyi/tongyi",
                            "name": "deepseek-v4-flash",
                            "mode": "chat",
                            "completion_params": {},
                        },
                        "system_prompt": "你是投诉处理专员。",
                        "user_prompt": "{{#sys.query#}}",
                    },
                },
                {
                    "id": "",
                    "type": "llm",
                    "title": "咨询处理",
                    "params": {
                        "model": {
                            "provider": "langgenius/tongyi/tongyi",
                            "name": "qwen3.5-plus",
                            "mode": "chat",
                            "completion_params": {},
                        },
                        "system_prompt": "你是咨询处理专员。",
                        "user_prompt": "{{#sys.query#}}",
                    },
                },
                {
                    "id": "",
                    "type": "answer",
                    "title": "投诉回复",
                    "params": {"answer": "{{#<complaint_llm_id>.text#}}"},
                },
                {
                    "id": "",
                    "type": "answer",
                    "title": "咨询回复",
                    "params": {"answer": "{{#<inquiry_llm_id>.text#}}"},
                },
            ],
            "edges": [
                {
                    "source_id": "<start_id>",
                    "target_id": "<classifier_id>",
                },
                {
                    "source_id": "<classifier_id>",
                    "target_id": "<complaint_llm_id>",
                    "source_handle": "complaint",
                },
                {
                    "source_id": "<classifier_id>",
                    "target_id": "<inquiry_llm_id>",
                    "source_handle": "inquiry",
                },
                {
                    "source_id": "<complaint_llm_id>",
                    "target_id": "<complaint_answer_id>",
                },
                {
                    "source_id": "<inquiry_llm_id>",
                    "target_id": "<inquiry_answer_id>",
                },
            ],
        },
        app_name="多模型售后分类",
        app_mode="advanced-chat",
    )
    plan = WorkflowPlan.model_validate(normalized.payload)
    by_id = {node.id: node for node in plan.nodes}

    assert set(by_id) == {
        "start",
        "classifier",
        "complaint_llm",
        "inquiry_llm",
        "complaint_answer",
        "inquiry_answer",
    }
    assert by_id["classifier"].params["query_variable_selector"] == [
        "start",
        "sys.query",
    ]
    assert by_id["complaint_llm"].params["model_provider"] == (
        "langgenius/tongyi/tongyi"
    )
    assert by_id["complaint_llm"].params["model_name"] == "deepseek-v4-flash"
    assert by_id["complaint_answer"].params["answer"] == (
        "{{#complaint_llm.text#}}"
    )
    assert preflight_plan(
        plan,
        compiler=_compiler(),
        expected_dsl_version="9.9.9",
    ).ok is True
