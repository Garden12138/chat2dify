from __future__ import annotations

import argparse
import json

from app.agent.planner import WorkflowPlanner
from app.compiler.dify import DifyDslCompiler
from app.config import load_settings
from app.dify.client import DifyClient
from app.dify.version import read_dify_version_info
from app.validator import validate_dsl, validate_plan


def main() -> None:
    parser = argparse.ArgumentParser(prog="chat2dify")
    subcommands = parser.add_subparsers(dest="command", required=True)

    draft_parser = subcommands.add_parser("draft")
    draft_parser.add_argument("message")
    draft_parser.add_argument("--app-name")

    create_parser = subcommands.add_parser("create")
    create_parser.add_argument("message")
    create_parser.add_argument("--app-name")

    args = parser.parse_args()
    settings = load_settings()
    version_info = read_dify_version_info(settings.dify_source_path)
    plan = WorkflowPlanner(settings).generate_plan(args.message, app_name=args.app_name)
    compiler = DifyDslCompiler(
        dsl_version=version_info.app_dsl_version,
        default_model_provider=settings.dify_default_model_provider,
        default_model_name=settings.dify_default_model_name,
        default_dataset_ids=settings.dify_default_dataset_ids,
    )
    dsl = compiler.compile(plan)
    issues = [*validate_plan(plan), *validate_dsl(dsl, expected_dsl_version=version_info.app_dsl_version)]
    if issues:
        print(json.dumps({"ok": False, "issues": [issue.model_dump() for issue in issues]}, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    if args.command == "draft":
        print(dsl)
        return

    with DifyClient(settings) as client:
        result = client.import_yaml(dsl, name=args.app_name or plan.name)
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
