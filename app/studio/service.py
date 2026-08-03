from __future__ import annotations

from app.agent.service import AgentApplicationService
from app.agent.store import AgentStore
from app.agent.state import RunConstraints
from app.studio.build import BuildCommandMode, ContextCommand, StudioBuildService
from app.studio.blueprints import StudioBlueprintService
from app.studio.home import StudioHomeService
from app.studio.identity import (
    AuthenticatedStudioRequest,
    IssuedStudioSession,
    StudioIdentityService,
    StudioHostUnavailable,
)
from app.studio.models import StudioHome


class StudioApplicationService:
    def __init__(
        self,
        *,
        identity: StudioIdentityService,
        home: StudioHomeService,
        build: StudioBuildService | None = None,
        blueprints: StudioBlueprintService | None = None,
    ) -> None:
        self.identity = identity
        self.home_service = home
        self.build_service = build
        self.blueprint_service = blueprints

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
