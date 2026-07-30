from __future__ import annotations

from app.agent.service import AgentApplicationService
from app.agent.store import AgentStore
from app.studio.home import StudioHomeService
from app.studio.identity import (
    AuthenticatedStudioRequest,
    IssuedStudioSession,
    StudioIdentityService,
)
from app.studio.models import StudioHome


class StudioApplicationService:
    def __init__(
        self,
        *,
        identity: StudioIdentityService,
        home: StudioHomeService,
    ) -> None:
        self.identity = identity
        self.home_service = home

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
