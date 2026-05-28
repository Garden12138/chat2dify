from app.dify.sse import SseParseIssue, iter_sse_events, summarize_events, terminal_event


def test_iter_sse_events_parses_data_events_and_ignores_ping() -> None:
    parsed = list(
        iter_sse_events(
            [
                ": keepalive",
                "data: ping",
                "",
                'data: {"event":"workflow_started","workflow_run_id":"run-1"}',
                "",
                'data: {"event":"workflow_finished","workflow_run_id":"run-1","data":{"status":"succeeded"}}',
                "",
            ]
        )
    )

    assert [item["event"] for item in parsed if isinstance(item, dict)] == ["workflow_started", "workflow_finished"]
    assert terminal_event([item for item in parsed if isinstance(item, dict)])["event"] == "workflow_finished"


def test_iter_sse_events_handles_multiline_json_and_parse_errors() -> None:
    parsed = list(
        iter_sse_events(
            [
                'data: {"event":',
                'data: "node_finished"}',
                "",
                "data: {bad",
                "",
            ]
        )
    )

    assert parsed[0] == {"event": "node_finished"}
    assert isinstance(parsed[1], SseParseIssue)


def test_summarize_events_counts_nodes_and_parse_errors() -> None:
    summary = summarize_events(
        [
            {"event": "workflow_started"},
            {"event": "node_started"},
            {"event": "node_finished"},
            {"event": "workflow_finished"},
        ],
        [SseParseIssue(message="bad", raw="{bad")],
    )

    assert summary["events"] == 4
    assert summary["node_started"] == 1
    assert summary["node_finished"] == 1
    assert summary["event_counts"]["workflow_finished"] == 1
    assert summary["parse_errors"] == 1
