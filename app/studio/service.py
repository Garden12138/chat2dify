from __future__ import annotations

from app.agent.service import AgentApplicationService
from app.agent.store import AgentStore
from app.agent.state import RunConstraints
from app.studio.build import BuildCommandMode, ContextCommand, StudioBuildService
from app.studio.automation import StudioRunAutomationService
from app.studio.mcp import StudioMcpService, StudioScopedTokenService
from app.studio.blueprints import StudioBlueprintService
from app.studio.home import StudioHomeService
from app.studio.identity import (
    AuthenticatedStudioRequest,
    IssuedStudioSession,
    StudioIdentityService,
    StudioHostUnavailable,
)
from app.studio.models import StudioHome
from app.studio.releases import StudioReleaseService
from app.studio.reviews import StudioReviewService
from app.studio.runs import StudioRunService
from app.studio.scenarios import StudioScenarioService


class StudioApplicationService:
    def __init__(
        self,
        *,
        identity: StudioIdentityService,
        home: StudioHomeService,
        build: StudioBuildService | None = None,
        blueprints: StudioBlueprintService | None = None,
        scenarios: StudioScenarioService | None = None,
        reviews: StudioReviewService | None = None,
        releases: StudioReleaseService | None = None,
        runs: StudioRunService | None = None,
        automation: StudioRunAutomationService | None = None,
        tokens: StudioScopedTokenService | None = None,
        mcp: StudioMcpService | None = None,
    ) -> None:
        self.identity = identity
        self.home_service = home
        self.build_service = build
        self.blueprint_service = blueprints
        self.scenario_service = scenarios
        self.review_service = reviews
        self.release_service = releases
        self.run_service = runs
        self.automation_service = automation
        self.token_service = tokens
        self.mcp_service = mcp

    def close(self) -> None:
        if self.scenario_service is not None:
            self.scenario_service.close()

    def issue_session(
        self,
        *,
        nonce: str,
        origin_header: str | None,
        cookie_header: str | None,
    ) -> IssuedStudioSession:
        return self.identity.issue(
            nonce=nonce,
            origin_header=origin_header,
            cookie_header=cookie_header,
        )

    def authenticate(
        self,
        *,
        authorization: str | None,
        origin_header: str | None,
        referer_header: str | None,
        cookie_header: str | None,
        app_name: str | None = None,
        app_mode: str | None = None,
    ) -> AuthenticatedStudioRequest:
        return self.identity.authenticate(
            authorization=authorization,
            origin_header=origin_header,
            referer_header=referer_header,
            cookie_header=cookie_header,
            app_name=app_name,
            app_mode=app_mode,
        )

    def home(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str | None,
        search: str | None,
        app_mode: str | None,
        v4_enabled: bool,
    ) -> StudioHome:
        return self.home_service.home(
            authenticated,
            project_id=project_id,
            search=search,
            app_mode=app_mode,
            v4_enabled=v4_enabled,
        )

    def resume_v4(
        self,
        authenticated: AuthenticatedStudioRequest,
        *,
        project_id: str,
        run_id: str,
        message: str | None,
        agent_store: AgentStore | None,
        agent_service: AgentApplicationService | None,
    ):
        return self.home_service.resume_v4(
            authenticated,
            project_id=project_id,
            run_id=run_id,
            message=message,
            agent_store=agent_store,
            agent_service=agent_service,
        )

    def require_build(self) -> StudioBuildService:
        if self.build_service is None:
            raise StudioHostUnavailable("Build Studio requires the v4 safety core.")
        return self.build_service

    def create_build(self, authenticated, **kwargs):
        return self.require_build().create(authenticated, **kwargs)

    def get_build(self, authenticated, **kwargs):
        return self.require_build().get(authenticated, **kwargs)

    def command_build(
        self,
        authenticated,
        *,
        mode: BuildCommandMode,
        constraints: RunConstraints | None = None,
        **kwargs,
    ):
        return self.require_build().command(
            authenticated,
            mode=mode,
            constraints=constraints,
            **kwargs,
        )

    def select_candidate(self, authenticated, **kwargs):
        return self.require_build().select(authenticated, **kwargs)

    def cancel_candidate(self, authenticated, **kwargs):
        return self.require_build().cancel_candidate(authenticated, **kwargs)

    def resume_candidate(self, authenticated, **kwargs):
        return self.require_build().resume_candidate(authenticated, **kwargs)

    def contextual_command(
        self,
        authenticated,
        *,
        command: ContextCommand,
        **kwargs,
    ):
        return self.require_build().contextual_command(
            authenticated,
            command=command,
            **kwargs,
        )

    def require_blueprints(self) -> StudioBlueprintService:
        if self.blueprint_service is None:
            raise StudioHostUnavailable("Blueprint Gallery requires the v4 safety core.")
        return self.blueprint_service

    def blueprint_gallery(self, authenticated, **kwargs):
        return self.require_blueprints().gallery(authenticated, **kwargs)

    def blueprint_detail(self, authenticated, **kwargs):
        return self.require_blueprints().detail(authenticated, **kwargs)

    def validate_blueprint_setup(self, authenticated, **kwargs):
        return self.require_blueprints().validate_setup(authenticated, **kwargs)

    def apply_blueprint(self, authenticated, **kwargs):
        return self.require_blueprints().apply(authenticated, **kwargs)

    def extract_blueprint(self, authenticated, **kwargs):
        return self.require_blueprints().extract(authenticated, **kwargs)

    def propose_blueprint_version(self, authenticated, **kwargs):
        return self.require_blueprints().propose_version(authenticated, **kwargs)

    def review_blueprint_version(self, authenticated, **kwargs):
        return self.require_blueprints().review_version(authenticated, **kwargs)

    def blueprint_upgrade_preview(self, authenticated, **kwargs):
        return self.require_blueprints().upgrade_preview(authenticated, **kwargs)

    def require_scenarios(self) -> StudioScenarioService:
        if self.scenario_service is None:
            raise StudioHostUnavailable("Scenario Lab requires the v4 safety core.")
        return self.scenario_service

    def scenario_lab(self, authenticated, **kwargs):
        return self.require_scenarios().lab(authenticated, **kwargs)

    def discover_scenario_input_schema(self, authenticated, **kwargs):
        return self.require_scenarios().discover_input_schema(authenticated, **kwargs)

    def create_scenario_suite(self, authenticated, **kwargs):
        return self.require_scenarios().create_suite(authenticated, **kwargs)

    def generate_scenario_edge_cases(self, authenticated, **kwargs):
        return self.require_scenarios().generate_edge_cases(authenticated, **kwargs)

    def approve_scenario_file_fixture(self, authenticated, **kwargs):
        return self.require_scenarios().approve_file_fixture(authenticated, **kwargs)

    def approve_sanitized_run_source(self, authenticated, **kwargs):
        return self.require_scenarios().approve_sanitized_run_source(
            authenticated,
            **kwargs,
        )

    def run_scenario_suite(self, authenticated, **kwargs):
        return self.require_scenarios().run_suite(authenticated, **kwargs)

    def cancel_scenario_run(self, authenticated, **kwargs):
        return self.require_scenarios().cancel_run(authenticated, **kwargs)

    def get_scenario_run(self, authenticated, **kwargs):
        return self.require_scenarios().get_run(authenticated, **kwargs)

    def cleanup_preview_fixture(self, authenticated, **kwargs):
        return self.require_scenarios().cleanup_fixture(authenticated, **kwargs)

    def reap_preview_fixtures(self, authenticated, **kwargs):
        return self.require_scenarios().reap_expired(authenticated, **kwargs)

    def save_scenario_baseline(self, authenticated, **kwargs):
        return self.require_scenarios().save_baseline(authenticated, **kwargs)

    def configure_regression_gate(self, authenticated, **kwargs):
        return self.require_scenarios().configure_gate(authenticated, **kwargs)

    def require_reviews(self) -> StudioReviewService:
        if self.review_service is None:
            raise StudioHostUnavailable("Review Center requires the v4 safety core.")
        return self.review_service

    def review_list(self, authenticated, **kwargs):
        return self.require_reviews().list(authenticated, **kwargs)

    def review_detail(self, authenticated, **kwargs):
        return self.require_reviews().detail(authenticated, **kwargs)

    def create_change_request(self, authenticated, **kwargs):
        return self.require_reviews().create(authenticated, **kwargs)

    def comment_change_request(self, authenticated, **kwargs):
        return self.require_reviews().comment(authenticated, **kwargs)

    def assign_change_request(self, authenticated, **kwargs):
        return self.require_reviews().assign(authenticated, **kwargs)

    def decide_change_request(self, authenticated, **kwargs):
        return self.require_reviews().decide(authenticated, **kwargs)

    def supersede_change_request(self, authenticated, **kwargs):
        return self.require_reviews().supersede(authenticated, **kwargs)

    def propose_rollback(self, authenticated, **kwargs):
        return self.require_reviews().propose_rollback(authenticated, **kwargs)

    def artifact_git_bundle(self, authenticated, **kwargs):
        return self.require_reviews().git_bundle(authenticated, **kwargs)

    def pull_git_artifact(self, authenticated, **kwargs):
        return self.require_reviews().git_pull(authenticated, **kwargs)

    def require_releases(self) -> StudioReleaseService:
        if self.release_service is None:
            raise StudioHostUnavailable("Release Center requires the safe Commit boundary.")
        return self.release_service

    def release_center(self, authenticated, **kwargs):
        return self.require_releases().center(authenticated, **kwargs)

    def create_logical_app(self, authenticated, **kwargs):
        return self.require_releases().create_logical_app(authenticated, **kwargs)

    def create_release_environment(self, authenticated, **kwargs):
        return self.require_releases().create_environment(authenticated, **kwargs)

    def configure_release_mapping(self, authenticated, **kwargs):
        return self.require_releases().configure_mapping(authenticated, **kwargs)

    def release_preview(self, authenticated, **kwargs):
        return self.require_releases().preview(authenticated, **kwargs)

    def authorize_release(self, authenticated, **kwargs):
        return self.require_releases().authorize(authenticated, **kwargs)

    def execute_release(self, authenticated, **kwargs):
        return self.require_releases().execute(authenticated, **kwargs)

    def require_runs(self) -> StudioRunService:
        if self.run_service is None:
            raise StudioHostUnavailable(
                "Run Center requires Release evidence and the safe Build boundary."
            )
        return self.run_service

    def run_center(self, authenticated, **kwargs):
        return self.require_runs().center(authenticated, **kwargs)

    def refresh_run_evidence(self, authenticated, **kwargs):
        result = self.require_runs().refresh(authenticated, **kwargs)
        if self.automation_service is not None:
            self.automation_service.evaluate_alerts(
                authenticated,
                project_id=kwargs["project_id"],
            )
        return result

    def run_incident(self, authenticated, **kwargs):
        return self.require_runs().incident(authenticated, **kwargs)

    def create_repair_proposal(self, authenticated, **kwargs):
        return self.require_runs().create_repair(authenticated, **kwargs)

    def require_automation(self) -> StudioRunAutomationService:
        if self.automation_service is None:
            raise StudioHostUnavailable("Run automation is not configured.")
        return self.automation_service

    def run_automation(self, authenticated, **kwargs):
        return self.require_automation().view(authenticated, **kwargs)

    def configure_run_alert(self, authenticated, **kwargs):
        return self.require_automation().configure_alert(authenticated, **kwargs)

    def configure_scheduled_regression(self, authenticated, **kwargs):
        return self.require_automation().configure_schedule(authenticated, **kwargs)

    def tick_run_automation(self, authenticated, **kwargs):
        automation = self.require_automation()
        project_id = kwargs["project_id"]
        alerts = automation.evaluate_alerts(
            authenticated,
            project_id=project_id,
        )
        schedules = automation.enqueue_due_schedules(
            authenticated,
            project_id=project_id,
        )
        return {"alerts_enqueued": alerts, "schedules_enqueued": schedules}

    def cancel_durable_work(self, authenticated, **kwargs):
        return self.require_automation().cancel_work(authenticated, **kwargs)

    def require_tokens(self) -> StudioScopedTokenService:
        if self.token_service is None:
            raise StudioHostUnavailable("Scoped API tokens are not configured.")
        return self.token_service

    def issue_scoped_token(self, authenticated, **kwargs):
        return self.require_tokens().issue(authenticated, **kwargs)

    def list_scoped_tokens(self, authenticated, **kwargs):
        return self.require_tokens().list(authenticated, **kwargs)

    def rotate_scoped_token(self, authenticated, **kwargs):
        return self.require_tokens().rotate(authenticated, **kwargs)

    def revoke_scoped_token(self, authenticated, **kwargs):
        return self.require_tokens().revoke(authenticated, **kwargs)

    def require_mcp(self) -> StudioMcpService:
        if self.mcp_service is None:
            raise StudioHostUnavailable("Safe MCP is not configured.")
        return self.mcp_service
