from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import NAMESPACE_URL, uuid5

from app.agent.trace import redact_sensitive_data
from app.studio.models import (
    Activity,
    BlueprintApplication,
    BlueprintDefinition,
    BlueprintVersionRecord,
    CandidateStatus,
    DurableJob,
    ExternalReceipt,
    Membership,
    OutboxMessage,
    PreviewEnvironment,
    PreviewFixture,
    Principal,
    Project,
    RegressionGate,
    ScenarioBaseline,
    ScenarioEvidenceBinding,
    ScenarioFileFixture,
    ScenarioRun,
    ScenarioSanitizedRunApproval,
    ScenarioSuite,
    StudioBuild,
    StudioCandidate,
    StudioRole,
    StudioSession,
    new_id,
    utc_now,
)


class StudioStoreError(RuntimeError):
    code = "STUDIO_STORE_ERROR"


class StudioStoreUnavailable(StudioStoreError):
    code = "STUDIO_STORE_UNAVAILABLE"


class StudioAccessDenied(StudioStoreError):
    code = "STUDIO_PROJECT_ACCESS_DENIED"


class StudioConflict(StudioStoreError):
    code = "STUDIO_VERSION_CONFLICT"


class StudioReplayDetected(StudioStoreError):
    code = "STUDIO_IDENTITY_REPLAY"


class StudioRecordNotFound(StudioStoreError):
    code = "STUDIO_RECORD_NOT_FOUND"


_MIGRATION_VERSION = 4
_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS studio_schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_projects (
        id TEXT PRIMARY KEY,
        slug TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        dify_tenant_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_memberships (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        principal_key TEXT NOT NULL,
        role TEXT NOT NULL,
        version INTEGER NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE(project_id, principal_key),
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_identity_nonces (
        issuer TEXT NOT NULL,
        nonce_hash TEXT NOT NULL,
        origin TEXT NOT NULL,
        expires_at REAL NOT NULL,
        consumed_at REAL NOT NULL,
        PRIMARY KEY(issuer, nonce_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_identity_sessions (
        id TEXT PRIMARY KEY,
        jti_hash TEXT NOT NULL UNIQUE,
        principal_key TEXT NOT NULL,
        project_id TEXT NOT NULL,
        dify_account_id TEXT NOT NULL,
        dify_tenant_id TEXT NOT NULL,
        origin TEXT NOT NULL,
        nonce_hash TEXT NOT NULL,
        expires_at REAL NOT NULL,
        created_at REAL NOT NULL,
        revoked_at REAL,
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_project_apps (
        project_id TEXT NOT NULL,
        app_id TEXT NOT NULL,
        linked_by TEXT NOT NULL,
        created_at REAL NOT NULL,
        PRIMARY KEY(project_id, app_id),
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_v4_links (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        linked_by TEXT NOT NULL,
        created_at REAL NOT NULL,
        UNIQUE(project_id, session_id),
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_activity (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        principal_key TEXT NOT NULL,
        kind TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        summary_json TEXT NOT NULL,
        created_at REAL NOT NULL,
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_jobs (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL,
        attempts INTEGER NOT NULL,
        max_attempts INTEGER NOT NULL,
        lease_owner TEXT,
        lease_expires_at REAL,
        idempotency_key TEXT NOT NULL,
        version INTEGER NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE(project_id, kind, idempotency_key),
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_outbox (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        topic TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL,
        attempts INTEGER NOT NULL,
        max_attempts INTEGER NOT NULL,
        lease_owner TEXT,
        lease_expires_at REAL,
        idempotency_key TEXT NOT NULL,
        version INTEGER NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE(project_id, topic, idempotency_key),
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_receipts (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        operation TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        outcome TEXT NOT NULL,
        external_ref TEXT,
        details_json TEXT NOT NULL,
        created_at REAL NOT NULL,
        UNIQUE(project_id, operation, idempotency_key),
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_builds (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        created_by TEXT NOT NULL,
        operation TEXT NOT NULL,
        entry_source TEXT NOT NULL,
        app_id TEXT,
        app_mode TEXT NOT NULL,
        app_name TEXT NOT NULL,
        base_fingerprint TEXT,
        selected_candidate_id TEXT,
        status TEXT NOT NULL,
        version INTEGER NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_candidates (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        build_id TEXT NOT NULL,
        run_id TEXT NOT NULL UNIQUE,
        label TEXT NOT NULL,
        intent TEXT NOT NULL,
        source_candidate_ids_json TEXT NOT NULL,
        base_fingerprint TEXT,
        status TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        version INTEGER NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE(build_id, ordinal),
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE,
        FOREIGN KEY(build_id) REFERENCES studio_builds(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_blueprints (
        id TEXT PRIMARY KEY,
        project_id TEXT,
        slug TEXT NOT NULL,
        visibility TEXT NOT NULL,
        created_by TEXT NOT NULL,
        current_version TEXT,
        status TEXT NOT NULL,
        version INTEGER NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE(project_id, slug),
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_blueprint_versions (
        id TEXT PRIMARY KEY,
        blueprint_id TEXT NOT NULL,
        project_id TEXT,
        semantic_version TEXT NOT NULL,
        status TEXT NOT NULL,
        definition_json TEXT NOT NULL,
        template_json TEXT NOT NULL,
        created_by TEXT NOT NULL,
        reviewed_by TEXT,
        review_note TEXT,
        created_at REAL NOT NULL,
        reviewed_at REAL,
        UNIQUE(blueprint_id, semantic_version),
        FOREIGN KEY(blueprint_id) REFERENCES studio_blueprints(id) ON DELETE CASCADE,
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_blueprint_applications (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        build_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL,
        blueprint_id TEXT NOT NULL,
        blueprint_version TEXT NOT NULL,
        setup_hash TEXT NOT NULL,
        applied_by TEXT NOT NULL,
        applied_at REAL NOT NULL,
        UNIQUE(project_id, candidate_id),
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE,
        FOREIGN KEY(build_id) REFERENCES studio_builds(id) ON DELETE CASCADE,
        FOREIGN KEY(candidate_id) REFERENCES studio_candidates(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_scenario_suites (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        build_id TEXT NOT NULL,
        name TEXT NOT NULL,
        semantic_version TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        owner_key TEXT NOT NULL,
        created_at REAL NOT NULL,
        UNIQUE(project_id, build_id, name, semantic_version),
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE,
        FOREIGN KEY(build_id) REFERENCES studio_builds(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_scenario_file_fixtures (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        name TEXT NOT NULL,
        opaque_ref TEXT NOT NULL,
        media_type TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        content_hash TEXT NOT NULL,
        approved_by TEXT NOT NULL,
        expires_at REAL NOT NULL,
        created_at REAL NOT NULL,
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_scenario_sanitized_run_sources (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        source_run_id TEXT NOT NULL,
        evidence_hash TEXT NOT NULL,
        approved_by TEXT NOT NULL,
        expires_at REAL NOT NULL,
        created_at REAL NOT NULL,
        UNIQUE(project_id, source_run_id, evidence_hash),
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_preview_environments (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        target_key TEXT NOT NULL,
        name TEXT NOT NULL,
        enabled INTEGER NOT NULL,
        default_ttl_seconds INTEGER NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE(project_id, target_key),
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_scenario_runs (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        build_id TEXT NOT NULL,
        suite_id TEXT NOT NULL,
        environment_id TEXT NOT NULL,
        candidate_ids_json TEXT NOT NULL,
        mappings_json TEXT NOT NULL,
        policy_json TEXT NOT NULL,
        authorized_by TEXT NOT NULL,
        status TEXT NOT NULL,
        cancel_requested INTEGER NOT NULL,
        reports_json TEXT NOT NULL,
        comparison_json TEXT,
        failure_json TEXT,
        cleanup_verified INTEGER NOT NULL,
        version INTEGER NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE,
        FOREIGN KEY(build_id) REFERENCES studio_builds(id) ON DELETE CASCADE,
        FOREIGN KEY(suite_id) REFERENCES studio_scenario_suites(id) ON DELETE CASCADE,
        FOREIGN KEY(environment_id) REFERENCES studio_preview_environments(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_preview_fixtures (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        scenario_run_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL,
        environment_id TEXT NOT NULL,
        label TEXT NOT NULL,
        status TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        import_id TEXT,
        app_id TEXT,
        receipt_json TEXT NOT NULL,
        cleanup_attempts INTEGER NOT NULL,
        absence_verified_at REAL,
        expires_at REAL NOT NULL,
        version INTEGER NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE(project_id, idempotency_key),
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE,
        FOREIGN KEY(scenario_run_id) REFERENCES studio_scenario_runs(id) ON DELETE CASCADE,
        FOREIGN KEY(environment_id) REFERENCES studio_preview_environments(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_scenario_baselines (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        build_id TEXT NOT NULL,
        suite_id TEXT NOT NULL,
        report_run_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL,
        binding_json TEXT NOT NULL,
        report_hash TEXT NOT NULL,
        saved_by TEXT NOT NULL,
        created_at REAL NOT NULL,
        UNIQUE(project_id, build_id, suite_id),
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE,
        FOREIGN KEY(build_id) REFERENCES studio_builds(id) ON DELETE CASCADE,
        FOREIGN KEY(suite_id) REFERENCES studio_scenario_suites(id) ON DELETE CASCADE,
        FOREIGN KEY(report_run_id) REFERENCES studio_scenario_runs(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS studio_regression_gates (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        build_id TEXT NOT NULL,
        suite_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE(project_id, build_id),
        FOREIGN KEY(project_id) REFERENCES studio_projects(id) ON DELETE CASCADE,
        FOREIGN KEY(build_id) REFERENCES studio_builds(id) ON DELETE CASCADE,
        FOREIGN KEY(suite_id) REFERENCES studio_scenario_suites(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_studio_memberships_principal
        ON studio_memberships(principal_key, updated_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_studio_activity_project
        ON studio_activity(project_id, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_studio_jobs_claim
        ON studio_jobs(status, lease_expires_at, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_studio_outbox_claim
        ON studio_outbox(status, lease_expires_at, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_studio_builds_project
        ON studio_builds(project_id, updated_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_studio_candidates_build
        ON studio_candidates(build_id, ordinal)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_studio_blueprints_project
        ON studio_blueprints(project_id, visibility, updated_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_studio_blueprint_versions_lookup
        ON studio_blueprint_versions(blueprint_id, status, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_studio_blueprint_applications_build
        ON studio_blueprint_applications(build_id, applied_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_studio_scenario_suites_build
        ON studio_scenario_suites(build_id, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_studio_scenario_runs_build
        ON studio_scenario_runs(build_id, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_studio_preview_fixtures_cleanup
        ON studio_preview_fixtures(status, expires_at, updated_at)
    """,
]


class StudioStore:
    """Small portable repository for SQLite local and PostgreSQL team modes."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        if database_url.startswith("sqlite:///"):
            self.dialect = "sqlite"
            self.path = Path(database_url.removeprefix("sqlite:///"))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._postgres_dsn = None
        elif database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            self.dialect = "postgresql"
            self.path = None
            self._postgres_dsn = database_url.replace(
                "postgresql+psycopg://",
                "postgresql://",
                1,
            )
        else:
            raise StudioStoreUnavailable("Unsupported Studio database URL.")
        self.initialize()

    def _connect(self):
        if self.dialect == "sqlite":
            assert self.path is not None
            connection = sqlite3.connect(self.path, timeout=5.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA foreign_keys=ON")
            return connection
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise StudioStoreUnavailable(
                "PostgreSQL Studio storage requires psycopg."
            ) from exc
        return psycopg.connect(self._postgres_dsn, row_factory=dict_row)

    def _sql(self, statement: str) -> str:
        if self.dialect == "postgresql":
            return statement.replace("?", "%s")
        return statement

    @contextmanager
    def _transaction(self, *, immediate: bool = False) -> Iterator[Any]:
        connection = self._connect()
        try:
            if self.dialect == "sqlite":
                connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _reader(self) -> Iterator[Any]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _execute(self, connection: Any, statement: str, params: tuple[Any, ...] = ()):
        return connection.execute(self._sql(statement), params)

    def initialize(self) -> None:
        connection = self._connect()
        try:
            if self.dialect == "sqlite":
                connection.execute("PRAGMA journal_mode=WAL")
            for statement in _SCHEMA_STATEMENTS:
                self._execute(connection, statement)
            row = self._execute(
                connection,
                "SELECT version FROM studio_schema_migrations WHERE version = ?",
                (_MIGRATION_VERSION,),
            ).fetchone()
            if row is None:
                self._execute(
                    connection,
                    "INSERT INTO studio_schema_migrations(version, applied_at) VALUES (?, ?)",
                    (_MIGRATION_VERSION, _timestamp(utc_now())),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def schema_version(self) -> int:
        with self._reader() as connection:
            row = self._execute(
                connection,
                "SELECT MAX(version) AS version FROM studio_schema_migrations",
            ).fetchone()
        return int(_row_value(row, "version") or 0)

    def ensure_personal_project(
        self,
        principal: Principal,
    ) -> tuple[Project, Membership]:
        digest = hashlib.sha256(
            f"{principal.key}:{principal.dify_tenant_id}".encode("utf-8")
        ).hexdigest()
        project_id = str(uuid5(NAMESPACE_URL, f"chat2dify:personal:{digest}"))
        membership_id = str(
            uuid5(NAMESPACE_URL, f"chat2dify:membership:{project_id}:{principal.key}")
        )
        slug = f"personal-{digest[:20]}"
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            created = self._execute(
                connection,
                """
                INSERT INTO studio_projects(
                    id, slug, name, kind, dify_tenant_id, version, created_at, updated_at
                ) VALUES (?, ?, ?, 'personal', ?, 1, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    project_id,
                    slug,
                    f"{principal.display_name} 的 Studio",
                    principal.dify_tenant_id,
                    _timestamp(now),
                    _timestamp(now),
                ),
            ).rowcount
            self._execute(
                connection,
                """
                INSERT INTO studio_memberships(
                    id, project_id, principal_key, role, version, created_at, updated_at
                ) VALUES (?, ?, ?, 'owner', 1, ?, ?)
                ON CONFLICT(project_id, principal_key) DO NOTHING
                """,
                (
                    membership_id,
                    project_id,
                    principal.key,
                    _timestamp(now),
                    _timestamp(now),
                ),
            )
            if created:
                _insert_activity(
                    self,
                    connection,
                    project_id=project_id,
                    principal_key=principal.key,
                    kind="project.personal.created",
                    entity_type="project",
                    entity_id=project_id,
                    summary={"name": f"{principal.display_name} 的 Studio"},
                    now=now,
                )
        return self.get_project_for_principal(project_id, principal.key)

    def create_project(
        self,
        *,
        name: str,
        dify_tenant_id: str,
        owner: Principal,
        kind: str = "team",
    ) -> tuple[Project, Membership]:
        now = utc_now()
        project_id = new_id()
        slug = f"team-{hashlib.sha256(project_id.encode()).hexdigest()[:20]}"
        membership_id = new_id()
        with self._transaction(immediate=True) as connection:
            self._execute(
                connection,
                """
                INSERT INTO studio_projects(
                    id, slug, name, kind, dify_tenant_id, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    project_id,
                    slug,
                    name,
                    kind,
                    dify_tenant_id,
                    _timestamp(now),
                    _timestamp(now),
                ),
            )
            self._execute(
                connection,
                """
                INSERT INTO studio_memberships(
                    id, project_id, principal_key, role, version, created_at, updated_at
                ) VALUES (?, ?, ?, 'owner', 1, ?, ?)
                """,
                (
                    membership_id,
                    project_id,
                    owner.key,
                    _timestamp(now),
                    _timestamp(now),
                ),
            )
            _insert_activity(
                self,
                connection,
                project_id=project_id,
                principal_key=owner.key,
                kind="project.created",
                entity_type="project",
                entity_id=project_id,
                summary={"name": name, "kind": kind},
                now=now,
            )
        return self.get_project_for_principal(project_id, owner.key)

    def add_membership(
        self,
        *,
        project_id: str,
        actor_key: str,
        principal_key: str,
        role: StudioRole,
    ) -> Membership:
        _, actor = self.get_project_for_principal(project_id, actor_key)
        if actor.role not in {"owner", "admin"}:
            raise StudioAccessDenied("Only a project owner or admin can add members.")
        now = utc_now()
        membership_id = new_id()
        with self._transaction(immediate=True) as connection:
            self._execute(
                connection,
                """
                INSERT INTO studio_memberships(
                    id, project_id, principal_key, role, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(project_id, principal_key) DO NOTHING
                """,
                (
                    membership_id,
                    project_id,
                    principal_key,
                    role,
                    _timestamp(now),
                    _timestamp(now),
                ),
            )
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_memberships
                WHERE project_id = ? AND principal_key = ?
                """,
                (project_id, principal_key),
            ).fetchone()
        assert row is not None
        return _membership_from_row(row)

    def get_project_for_principal(
        self,
        project_id: str,
        principal_key: str,
    ) -> tuple[Project, Membership]:
        with self._reader() as connection:
            membership_row = self._execute(
                connection,
                """
                SELECT * FROM studio_memberships
                WHERE project_id = ? AND principal_key = ?
                """,
                (project_id, principal_key),
            ).fetchone()
            if membership_row is None:
                raise StudioAccessDenied(
                    "You do not have access to this Studio project."
                )
            project_row = self._execute(
                connection,
                "SELECT * FROM studio_projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if project_row is None:
            raise StudioRecordNotFound(project_id)
        return _project_from_row(project_row), _membership_from_row(membership_row)

    def list_projects(self, principal_key: str) -> list[tuple[Project, Membership]]:
        with self._reader() as connection:
            rows = self._execute(
                connection,
                """
                SELECT
                    p.id AS p_id, p.slug AS p_slug, p.name AS p_name,
                    p.kind AS p_kind, p.dify_tenant_id AS p_dify_tenant_id,
                    p.version AS p_version, p.created_at AS p_created_at,
                    p.updated_at AS p_updated_at,
                    m.id AS m_id, m.project_id AS m_project_id,
                    m.principal_key AS m_principal_key, m.role AS m_role,
                    m.version AS m_version, m.created_at AS m_created_at,
                    m.updated_at AS m_updated_at
                FROM studio_memberships m
                JOIN studio_projects p ON p.id = m.project_id
                WHERE m.principal_key = ?
                ORDER BY p.updated_at DESC
                """,
                (principal_key,),
            ).fetchall()
        return [(_project_from_joined_row(row), _membership_from_joined_row(row)) for row in rows]

    def rename_project(
        self,
        *,
        project_id: str,
        principal_key: str,
        name: str,
        expected_version: int,
    ) -> Project:
        _, membership = self.get_project_for_principal(project_id, principal_key)
        if membership.role not in {"owner", "admin"}:
            raise StudioAccessDenied("Only a project owner or admin can rename it.")
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            cursor = self._execute(
                connection,
                """
                UPDATE studio_projects
                SET name = ?, version = version + 1, updated_at = ?
                WHERE id = ? AND version = ?
                """,
                (name, _timestamp(now), project_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise StudioConflict("The project changed; reload before retrying.")
        project, _ = self.get_project_for_principal(project_id, principal_key)
        return project

    def consume_identity_nonce(
        self,
        *,
        issuer: str,
        nonce: str,
        origin: str,
        expires_at: datetime,
    ) -> str:
        nonce_hash = _hash_value(nonce)
        now = utc_now()
        try:
            with self._transaction(immediate=True) as connection:
                self._execute(
                    connection,
                    """
                    INSERT INTO studio_identity_nonces(
                        issuer, nonce_hash, origin, expires_at, consumed_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        issuer,
                        nonce_hash,
                        origin,
                        _timestamp(expires_at),
                        _timestamp(now),
                    ),
                )
        except Exception as exc:
            if _is_unique_violation(exc):
                raise StudioReplayDetected(
                    "This Dify-host Studio nonce was already used."
                ) from exc
            raise
        return nonce_hash

    def create_identity_session(
        self,
        *,
        jti: str,
        principal: Principal,
        project_id: str,
        origin: str,
        nonce_hash: str,
        expires_at: datetime,
    ) -> StudioSession:
        now = utc_now()
        session = StudioSession(
            id=new_id(),
            jti_hash=_hash_value(jti),
            principal_key=principal.key,
            project_id=project_id,
            dify_account_id=principal.subject,
            dify_tenant_id=principal.dify_tenant_id,
            origin=origin,
            nonce_hash=nonce_hash,
            expires_at=expires_at,
            created_at=now,
        )
        with self._transaction() as connection:
            self._execute(
                connection,
                """
                INSERT INTO studio_identity_sessions(
                    id, jti_hash, principal_key, project_id, dify_account_id,
                    dify_tenant_id, origin, nonce_hash, expires_at, created_at,
                    revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    session.id,
                    session.jti_hash,
                    session.principal_key,
                    session.project_id,
                    session.dify_account_id,
                    session.dify_tenant_id,
                    session.origin,
                    session.nonce_hash,
                    _timestamp(session.expires_at),
                    _timestamp(session.created_at),
                ),
            )
        return session

    def get_identity_session(self, jti: str) -> StudioSession:
        with self._reader() as connection:
            row = self._execute(
                connection,
                "SELECT * FROM studio_identity_sessions WHERE jti_hash = ?",
                (_hash_value(jti),),
            ).fetchone()
        if row is None:
            raise StudioAccessDenied("The Studio session is not recognized.")
        return _session_from_row(row)

    def revoke_identity_session(self, jti: str) -> None:
        with self._transaction(immediate=True) as connection:
            self._execute(
                connection,
                """
                UPDATE studio_identity_sessions
                SET revoked_at = ?
                WHERE jti_hash = ? AND revoked_at IS NULL
                """,
                (_timestamp(utc_now()), _hash_value(jti)),
            )

    def link_project_app(
        self,
        *,
        project_id: str,
        principal_key: str,
        app_id: str,
    ) -> None:
        self.get_project_for_principal(project_id, principal_key)
        with self._transaction() as connection:
            self._execute(
                connection,
                """
                INSERT INTO studio_project_apps(project_id, app_id, linked_by, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(project_id, app_id) DO NOTHING
                """,
                (project_id, app_id, principal_key, _timestamp(utc_now())),
            )

    def list_project_app_ids(self, project_id: str, principal_key: str) -> set[str]:
        self.get_project_for_principal(project_id, principal_key)
        with self._reader() as connection:
            rows = self._execute(
                connection,
                "SELECT app_id FROM studio_project_apps WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        return {str(_row_value(row, "app_id")) for row in rows}

    def link_v4_sessions(
        self,
        *,
        project_id: str,
        principal_key: str,
        session_ids: list[str],
    ) -> int:
        self.get_project_for_principal(project_id, principal_key)
        linked = 0
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            for session_id in session_ids:
                cursor = self._execute(
                    connection,
                    """
                    INSERT INTO studio_v4_links(
                        id, project_id, session_id, linked_by, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, session_id) DO NOTHING
                    """,
                    (
                        new_id(),
                        project_id,
                        session_id,
                        principal_key,
                        _timestamp(now),
                    ),
                )
                linked += max(int(cursor.rowcount or 0), 0)
            if linked:
                _insert_activity(
                    self,
                    connection,
                    project_id=project_id,
                    principal_key=principal_key,
                    kind="migration.v4.linked",
                    entity_type="v4_session_batch",
                    entity_id=new_id(),
                    summary={"linked_session_count": linked},
                    now=now,
                )
        return linked

    def list_v4_session_ids(self, project_id: str, principal_key: str) -> list[str]:
        self.get_project_for_principal(project_id, principal_key)
        with self._reader() as connection:
            rows = self._execute(
                connection,
                """
                SELECT session_id FROM studio_v4_links
                WHERE project_id = ?
                ORDER BY created_at DESC
                """,
                (project_id,),
            ).fetchall()
        return [str(_row_value(row, "session_id")) for row in rows]

    def append_activity(
        self,
        *,
        project_id: str,
        principal_key: str,
        kind: str,
        entity_type: str,
        entity_id: str,
        summary: dict[str, Any],
    ) -> Activity:
        self.get_project_for_principal(project_id, principal_key)
        now = utc_now()
        activity_id = new_id()
        with self._transaction() as connection:
            _insert_activity(
                self,
                connection,
                project_id=project_id,
                principal_key=principal_key,
                kind=kind,
                entity_type=entity_type,
                entity_id=entity_id,
                summary=summary,
                now=now,
                activity_id=activity_id,
            )
        return Activity(
            id=activity_id,
            project_id=project_id,
            principal_key=principal_key,
            kind=kind,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=_safe_json(summary),
            created_at=now,
        )

    def list_activity(
        self,
        *,
        project_id: str,
        principal_key: str,
        limit: int = 50,
    ) -> list[Activity]:
        self.get_project_for_principal(project_id, principal_key)
        with self._reader() as connection:
            rows = self._execute(
                connection,
                """
                SELECT * FROM studio_activity
                WHERE project_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()
        return [_activity_from_row(row) for row in rows]

    def create_build(
        self,
        *,
        project_id: str,
        principal_key: str,
        operation: str,
        entry_source: str,
        app_id: str | None,
        app_mode: str,
        app_name: str,
    ) -> StudioBuild:
        _, membership = self.get_project_for_principal(project_id, principal_key)
        if membership.role not in {"owner", "admin", "builder"}:
            raise StudioAccessDenied("Only a project builder can start Build Studio work.")
        now = utc_now()
        build = StudioBuild(
            id=new_id(),
            project_id=project_id,
            created_by=principal_key,
            operation=operation,
            entry_source=entry_source,
            app_id=app_id,
            app_mode=app_mode,
            app_name=app_name,
            status="active",
            version=1,
            created_at=now,
            updated_at=now,
        )
        with self._transaction(immediate=True) as connection:
            self._execute(
                connection,
                """
                INSERT INTO studio_builds(
                    id, project_id, created_by, operation, entry_source,
                    app_id, app_mode, app_name, base_fingerprint,
                    selected_candidate_id, status, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'active', 1, ?, ?)
                """,
                (
                    build.id,
                    project_id,
                    principal_key,
                    operation,
                    entry_source,
                    app_id,
                    app_mode,
                    app_name,
                    _timestamp(now),
                    _timestamp(now),
                ),
            )
            _insert_activity(
                self,
                connection,
                project_id=project_id,
                principal_key=principal_key,
                kind="build.started",
                entity_type="build",
                entity_id=build.id,
                summary={
                    "operation": operation,
                    "app_id": app_id,
                    "app_mode": app_mode,
                    "entry_source": entry_source,
                },
                now=now,
            )
        return build

    def get_build(
        self,
        build_id: str,
        *,
        project_id: str,
        principal_key: str,
    ) -> StudioBuild:
        self.get_project_for_principal(project_id, principal_key)
        with self._reader() as connection:
            row = self._execute(
                connection,
                "SELECT * FROM studio_builds WHERE id = ? AND project_id = ?",
                (build_id, project_id),
            ).fetchone()
        if row is None:
            raise StudioRecordNotFound("The Build Studio work item was not found.")
        return _build_from_row(row)

    def add_candidate(
        self,
        *,
        build_id: str,
        project_id: str,
        principal_key: str,
        run_id: str,
        label: str,
        intent: str,
        source_candidate_ids: list[str] | None = None,
    ) -> StudioCandidate:
        build = self.get_build(
            build_id,
            project_id=project_id,
            principal_key=principal_key,
        )
        if build.status != "active":
            raise StudioConflict("The Build Studio work item is not active.")
        now = utc_now()
        candidate_id = new_id()
        sources = source_candidate_ids or []
        with self._transaction(immediate=True) as connection:
            row = self._execute(
                connection,
                "SELECT COALESCE(MAX(ordinal), 0) AS ordinal FROM studio_candidates WHERE build_id = ?",
                (build_id,),
            ).fetchone()
            ordinal = int(_row_value(row, "ordinal") or 0) + 1
            self._execute(
                connection,
                """
                INSERT INTO studio_candidates(
                    id, project_id, build_id, run_id, label, intent,
                    source_candidate_ids_json, base_fingerprint, status,
                    ordinal, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'queued', ?, 1, ?, ?)
                """,
                (
                    candidate_id,
                    project_id,
                    build_id,
                    run_id,
                    label,
                    intent,
                    _json_dump({"ids": sources}),
                    ordinal,
                    _timestamp(now),
                    _timestamp(now),
                ),
            )
            self._execute(
                connection,
                "UPDATE studio_builds SET version = version + 1, updated_at = ? WHERE id = ?",
                (_timestamp(now), build_id),
            )
            _insert_activity(
                self,
                connection,
                project_id=project_id,
                principal_key=principal_key,
                kind="candidate.started",
                entity_type="candidate",
                entity_id=candidate_id,
                summary={"build_id": build_id, "label": label, "sources": sources},
                now=now,
            )
            candidate_row = self._execute(
                connection,
                "SELECT * FROM studio_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
        assert candidate_row is not None
        return _candidate_from_row(candidate_row)

    def list_candidates(
        self,
        build_id: str,
        *,
        project_id: str,
        principal_key: str,
    ) -> list[StudioCandidate]:
        self.get_build(build_id, project_id=project_id, principal_key=principal_key)
        with self._reader() as connection:
            rows = self._execute(
                connection,
                """
                SELECT * FROM studio_candidates
                WHERE build_id = ? AND project_id = ?
                ORDER BY ordinal ASC
                """,
                (build_id, project_id),
            ).fetchall()
        return [_candidate_from_row(row) for row in rows]

    def get_candidate(
        self,
        candidate_id: str,
        *,
        build_id: str,
        project_id: str,
        principal_key: str,
    ) -> StudioCandidate:
        self.get_build(build_id, project_id=project_id, principal_key=principal_key)
        with self._reader() as connection:
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_candidates
                WHERE id = ? AND build_id = ? AND project_id = ?
                """,
                (candidate_id, build_id, project_id),
            ).fetchone()
        if row is None:
            raise StudioRecordNotFound("The Build Studio candidate was not found.")
        return _candidate_from_row(row)

    def get_candidate_for_project(
        self,
        candidate_id: str,
        *,
        project_id: str,
        principal_key: str,
    ) -> StudioCandidate:
        self.get_project_for_principal(project_id, principal_key)
        with self._reader() as connection:
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_candidates
                WHERE id = ? AND project_id = ?
                """,
                (candidate_id, project_id),
            ).fetchone()
        if row is None:
            raise StudioRecordNotFound("The Build Studio candidate was not found.")
        return _candidate_from_row(row)

    def reconcile_candidate(
        self,
        candidate_id: str,
        *,
        status: CandidateStatus,
        base_fingerprint: str | None,
    ) -> StudioCandidate:
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            row = self._execute(
                connection,
                "SELECT * FROM studio_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise StudioRecordNotFound("The Build Studio candidate was not found.")
            current = _candidate_from_row(row)
            if current.status == status and current.base_fingerprint == base_fingerprint:
                return current
            self._execute(
                connection,
                """
                UPDATE studio_candidates
                SET status = ?, base_fingerprint = ?, version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (status, base_fingerprint, _timestamp(now), candidate_id),
            )
            updated = self._execute(
                connection,
                "SELECT * FROM studio_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
        assert updated is not None
        return _candidate_from_row(updated)

    def bind_build_base(
        self,
        build_id: str,
        *,
        base_fingerprint: str,
    ) -> bool:
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            row = self._execute(
                connection,
                "SELECT base_fingerprint FROM studio_builds WHERE id = ?",
                (build_id,),
            ).fetchone()
            if row is None:
                raise StudioRecordNotFound("The Build Studio work item was not found.")
            current = _optional_string(_row_value(row, "base_fingerprint"))
            if current is not None:
                return current == base_fingerprint
            self._execute(
                connection,
                """
                UPDATE studio_builds
                SET base_fingerprint = ?, version = version + 1, updated_at = ?
                WHERE id = ? AND base_fingerprint IS NULL
                """,
                (base_fingerprint, _timestamp(now), build_id),
            )
        return True

    def select_candidate(
        self,
        candidate_id: str,
        *,
        build_id: str,
        project_id: str,
        principal_key: str,
    ) -> StudioBuild:
        candidate = self.get_candidate(
            candidate_id,
            build_id=build_id,
            project_id=project_id,
            principal_key=principal_key,
        )
        if candidate.status != "valid" or not candidate.base_fingerprint:
            raise StudioConflict("Only a valid, base-bound candidate can be selected.")
        build = self.get_build(build_id, project_id=project_id, principal_key=principal_key)
        if build.base_fingerprint != candidate.base_fingerprint:
            raise StudioConflict("The candidate no longer matches the pinned Build base.")
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            self._execute(
                connection,
                """
                UPDATE studio_builds
                SET selected_candidate_id = ?, version = version + 1, updated_at = ?
                WHERE id = ? AND project_id = ?
                """,
                (candidate.id, _timestamp(now), build_id, project_id),
            )
            _insert_activity(
                self,
                connection,
                project_id=project_id,
                principal_key=principal_key,
                kind="candidate.selected",
                entity_type="candidate",
                entity_id=candidate.id,
                summary={"build_id": build_id, "label": candidate.label},
                now=now,
            )
        return self.get_build(build_id, project_id=project_id, principal_key=principal_key)

    def create_blueprint(
        self,
        *,
        definition: BlueprintDefinition,
        template: dict[str, Any],
        principal_key: str,
        initial_status: str,
    ) -> BlueprintVersionRecord:
        if definition.visibility not in {"private", "team"} or not definition.project_id:
            raise StudioConflict("Only Project-scoped Private or Team Blueprints can be saved.")
        _, membership = self.get_project_for_principal(
            definition.project_id,
            principal_key,
        )
        if membership.role not in {"owner", "admin", "builder"}:
            raise StudioAccessDenied("Your project role cannot save Blueprints.")
        if initial_status not in {"published", "pending_review"}:
            raise ValueError("A saved Blueprint must be published or pending review.")
        now = utc_now()
        record_id = new_id()
        with self._transaction(immediate=True) as connection:
            self._execute(
                connection,
                """
                INSERT INTO studio_blueprints(
                    id, project_id, slug, visibility, created_by,
                    current_version, status, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    definition.id,
                    definition.project_id,
                    definition.slug,
                    definition.visibility,
                    principal_key,
                    definition.version if initial_status == "published" else None,
                    initial_status,
                    _timestamp(now),
                    _timestamp(now),
                ),
            )
            self._execute(
                connection,
                """
                INSERT INTO studio_blueprint_versions(
                    id, blueprint_id, project_id, semantic_version, status,
                    definition_json, template_json, created_by, reviewed_by,
                    review_note, created_at, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL)
                """,
                (
                    record_id,
                    definition.id,
                    definition.project_id,
                    definition.version,
                    initial_status,
                    _json_dump(definition.model_dump(mode="json")),
                    _json_dump(_safe_json(template)),
                    principal_key,
                    _timestamp(now),
                ),
            )
            _insert_activity(
                self,
                connection,
                project_id=definition.project_id,
                principal_key=principal_key,
                kind=(
                    "blueprint.saved.private"
                    if definition.visibility == "private"
                    else "blueprint.review.requested"
                ),
                entity_type="blueprint",
                entity_id=definition.id,
                summary={
                    "name": definition.name,
                    "version": definition.version,
                    "visibility": definition.visibility,
                    "status": initial_status,
                },
                now=now,
            )
        return self.get_blueprint_version(
            definition.id,
            definition.version,
            project_id=definition.project_id,
            principal_key=principal_key,
            include_unpublished=True,
        )[0]

    def list_published_blueprints(
        self,
        *,
        project_id: str,
        principal_key: str,
    ) -> list[tuple[BlueprintVersionRecord, dict[str, Any]]]:
        self.get_project_for_principal(project_id, principal_key)
        with self._reader() as connection:
            rows = self._execute(
                connection,
                """
                SELECT v.*, b.visibility AS blueprint_visibility,
                       b.created_by AS blueprint_created_by
                FROM studio_blueprints b
                JOIN studio_blueprint_versions v
                  ON v.blueprint_id = b.id
                 AND v.semantic_version = b.current_version
                WHERE b.project_id = ? AND v.status = 'published'
                  AND (
                    b.visibility = 'team'
                    OR (b.visibility = 'private' AND b.created_by = ?)
                  )
                ORDER BY b.updated_at DESC, b.id ASC
                """,
                (project_id, principal_key),
            ).fetchall()
        return [(_blueprint_version_from_row(row), _blueprint_template_from_row(row)) for row in rows]

    def list_pending_blueprints(
        self,
        *,
        project_id: str,
        principal_key: str,
    ) -> list[tuple[BlueprintVersionRecord, dict[str, Any]]]:
        _, membership = self.get_project_for_principal(project_id, principal_key)
        with self._reader() as connection:
            rows = self._execute(
                connection,
                """
                SELECT v.*
                FROM studio_blueprints b
                JOIN studio_blueprint_versions v ON v.blueprint_id = b.id
                WHERE b.project_id = ? AND b.visibility = 'team'
                  AND v.status = 'pending_review'
                  AND (
                    v.created_by = ?
                    OR ? IN ('owner', 'admin', 'reviewer')
                  )
                ORDER BY v.created_at DESC, v.id ASC
                """,
                (project_id, principal_key, membership.role),
            ).fetchall()
        return [
            (_blueprint_version_from_row(row), _blueprint_template_from_row(row))
            for row in rows
        ]

    def get_blueprint_version(
        self,
        blueprint_id: str,
        semantic_version: str | None,
        *,
        project_id: str,
        principal_key: str,
        include_unpublished: bool = False,
    ) -> tuple[BlueprintVersionRecord, dict[str, Any]]:
        _, membership = self.get_project_for_principal(project_id, principal_key)
        with self._reader() as connection:
            blueprint = self._execute(
                connection,
                "SELECT * FROM studio_blueprints WHERE id = ? AND project_id = ?",
                (blueprint_id, project_id),
            ).fetchone()
            if blueprint is None:
                raise StudioRecordNotFound("The Project Blueprint was not found.")
            visibility = str(_row_value(blueprint, "visibility"))
            created_by = str(_row_value(blueprint, "created_by"))
            if visibility == "private" and created_by != principal_key:
                raise StudioRecordNotFound("The Project Blueprint was not found.")
            version = semantic_version or _optional_string(
                _row_value(blueprint, "current_version")
            )
            if version is None:
                raise StudioConflict("This Blueprint has no published version yet.")
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_blueprint_versions
                WHERE blueprint_id = ? AND semantic_version = ?
                """,
                (blueprint_id, version),
            ).fetchone()
        if row is None:
            raise StudioRecordNotFound("The Blueprint version was not found.")
        status = str(_row_value(row, "status"))
        created_version_by = str(_row_value(row, "created_by"))
        if status != "published" and not (
            include_unpublished
            and (
                created_version_by == principal_key
                or membership.role in {"owner", "admin", "reviewer"}
            )
        ):
            raise StudioRecordNotFound("The Blueprint version was not found.")
        return _blueprint_version_from_row(row), _blueprint_template_from_row(row)

    def propose_blueprint_version(
        self,
        *,
        definition: BlueprintDefinition,
        template: dict[str, Any],
        principal_key: str,
    ) -> BlueprintVersionRecord:
        if not definition.project_id:
            raise StudioConflict("Project Blueprint versions require a Project.")
        _, membership = self.get_project_for_principal(
            definition.project_id,
            principal_key,
        )
        if membership.role not in {"owner", "admin", "builder"}:
            raise StudioAccessDenied("Your project role cannot propose Blueprint versions.")
        with self._reader() as connection:
            blueprint = self._execute(
                connection,
                "SELECT * FROM studio_blueprints WHERE id = ? AND project_id = ?",
                (definition.id, definition.project_id),
            ).fetchone()
        if blueprint is None:
            raise StudioRecordNotFound("The Project Blueprint was not found.")
        if (
            str(_row_value(blueprint, "created_by")) != principal_key
            and membership.role not in {"owner", "admin"}
        ):
            raise StudioAccessDenied("Only the Blueprint author or a Project admin can propose a version.")
        now = utc_now()
        record_id = new_id()
        with self._transaction(immediate=True) as connection:
            self._execute(
                connection,
                """
                INSERT INTO studio_blueprint_versions(
                    id, blueprint_id, project_id, semantic_version, status,
                    definition_json, template_json, created_by, reviewed_by,
                    review_note, created_at, reviewed_at
                ) VALUES (?, ?, ?, ?, 'pending_review', ?, ?, ?, NULL, NULL, ?, NULL)
                """,
                (
                    record_id,
                    definition.id,
                    definition.project_id,
                    definition.version,
                    _json_dump(definition.model_dump(mode="json")),
                    _json_dump(_safe_json(template)),
                    principal_key,
                    _timestamp(now),
                ),
            )
            self._execute(
                connection,
                """
                UPDATE studio_blueprints
                SET status = 'pending_review', version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (_timestamp(now), definition.id),
            )
            _insert_activity(
                self,
                connection,
                project_id=definition.project_id,
                principal_key=principal_key,
                kind="blueprint.version.review.requested",
                entity_type="blueprint",
                entity_id=definition.id,
                summary={"version": definition.version},
                now=now,
            )
        return self.get_blueprint_version(
            definition.id,
            definition.version,
            project_id=definition.project_id,
            principal_key=principal_key,
            include_unpublished=True,
        )[0]

    def review_blueprint_version(
        self,
        *,
        blueprint_id: str,
        semantic_version: str,
        project_id: str,
        principal_key: str,
        approved: bool,
        note: str,
    ) -> BlueprintVersionRecord:
        _, membership = self.get_project_for_principal(project_id, principal_key)
        if membership.role not in {"owner", "admin", "reviewer"}:
            raise StudioAccessDenied("Your project role cannot review Blueprint versions.")
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_blueprint_versions
                WHERE blueprint_id = ? AND project_id = ? AND semantic_version = ?
                """,
                (blueprint_id, project_id, semantic_version),
            ).fetchone()
            if row is None:
                raise StudioRecordNotFound("The Blueprint version was not found.")
            if str(_row_value(row, "status")) != "pending_review":
                raise StudioConflict("Only a pending Blueprint version can be reviewed.")
            if str(_row_value(row, "created_by")) == principal_key:
                raise StudioAccessDenied("A Blueprint version author cannot review their own version.")
            status = "published" if approved else "rejected"
            self._execute(
                connection,
                """
                UPDATE studio_blueprint_versions
                SET status = ?, reviewed_by = ?, review_note = ?, reviewed_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    principal_key,
                    str(redact_sensitive_data(note))[:2_000],
                    _timestamp(now),
                    str(_row_value(row, "id")),
                ),
            )
            if approved:
                self._execute(
                    connection,
                    """
                    UPDATE studio_blueprints
                    SET current_version = ?, status = 'published',
                        version = version + 1, updated_at = ?
                    WHERE id = ? AND project_id = ?
                    """,
                    (semantic_version, _timestamp(now), blueprint_id, project_id),
                )
            else:
                self._execute(
                    connection,
                    """
                    UPDATE studio_blueprints
                    SET status = CASE WHEN current_version IS NULL THEN 'rejected' ELSE 'published' END,
                        version = version + 1, updated_at = ?
                    WHERE id = ? AND project_id = ?
                    """,
                    (_timestamp(now), blueprint_id, project_id),
                )
            _insert_activity(
                self,
                connection,
                project_id=project_id,
                principal_key=principal_key,
                kind=(
                    "blueprint.version.published"
                    if approved
                    else "blueprint.version.rejected"
                ),
                entity_type="blueprint",
                entity_id=blueprint_id,
                summary={"version": semantic_version, "approved": approved},
                now=now,
            )
        return self.get_blueprint_version(
            blueprint_id,
            semantic_version,
            project_id=project_id,
            principal_key=principal_key,
            include_unpublished=True,
        )[0]

    def record_blueprint_application(
        self,
        *,
        project_id: str,
        principal_key: str,
        build_id: str,
        candidate_id: str,
        blueprint_id: str,
        blueprint_version: str,
        setup_hash: str,
    ) -> BlueprintApplication:
        self.get_candidate(
            candidate_id,
            build_id=build_id,
            project_id=project_id,
            principal_key=principal_key,
        )
        now = utc_now()
        application = BlueprintApplication(
            id=new_id(),
            project_id=project_id,
            build_id=build_id,
            candidate_id=candidate_id,
            blueprint_id=blueprint_id,
            blueprint_version=blueprint_version,
            setup_hash=setup_hash,
            applied_by=principal_key,
            applied_at=now,
        )
        with self._transaction(immediate=True) as connection:
            self._execute(
                connection,
                """
                INSERT INTO studio_blueprint_applications(
                    id, project_id, build_id, candidate_id, blueprint_id,
                    blueprint_version, setup_hash, applied_by, applied_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    application.id,
                    project_id,
                    build_id,
                    candidate_id,
                    blueprint_id,
                    blueprint_version,
                    setup_hash,
                    principal_key,
                    _timestamp(now),
                ),
            )
            _insert_activity(
                self,
                connection,
                project_id=project_id,
                principal_key=principal_key,
                kind="blueprint.applied",
                entity_type="candidate",
                entity_id=candidate_id,
                summary={
                    "build_id": build_id,
                    "blueprint_id": blueprint_id,
                    "blueprint_version": blueprint_version,
                },
                now=now,
            )
        return application

    def get_blueprint_application(
        self,
        application_id: str,
        *,
        project_id: str,
        principal_key: str,
    ) -> BlueprintApplication:
        self.get_project_for_principal(project_id, principal_key)
        with self._reader() as connection:
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_blueprint_applications
                WHERE id = ? AND project_id = ?
                """,
                (application_id, project_id),
            ).fetchone()
        if row is None:
            raise StudioRecordNotFound("The Blueprint application was not found.")
        return _blueprint_application_from_row(row)

    def create_scenario_suite(
        self,
        suite: ScenarioSuite,
        *,
        principal_key: str,
    ) -> ScenarioSuite:
        self.get_project_for_principal(suite.project_id, principal_key)
        try:
            with self._transaction(immediate=True) as connection:
                self._execute(
                    connection,
                    """
                    INSERT INTO studio_scenario_suites(
                        id, project_id, build_id, name, semantic_version,
                        payload_json, content_hash, owner_key, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        suite.id,
                        suite.project_id,
                        suite.build_id,
                        suite.name,
                        suite.semantic_version,
                        _json_dump(suite.model_dump(mode="json")),
                        suite.content_hash,
                        suite.owner_key,
                        _timestamp(suite.created_at),
                    ),
                )
                _insert_activity(
                    self,
                    connection,
                    project_id=suite.project_id,
                    principal_key=principal_key,
                    kind="scenario.suite.created",
                    entity_type="scenario_suite",
                    entity_id=suite.id,
                    summary={
                        "name": suite.name,
                        "version": suite.semantic_version,
                        "case_count": len(suite.cases),
                    },
                    now=suite.created_at,
                )
        except Exception as exc:
            if _is_unique_violation(exc):
                raise StudioConflict(
                    "A Scenario Suite with this name and semantic version already exists."
                ) from exc
            raise
        return suite

    def get_scenario_suite(
        self,
        suite_id: str,
        *,
        project_id: str,
        principal_key: str,
    ) -> ScenarioSuite:
        self.get_project_for_principal(project_id, principal_key)
        with self._reader() as connection:
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_scenario_suites
                WHERE id = ? AND project_id = ?
                """,
                (suite_id, project_id),
            ).fetchone()
        if row is None:
            raise StudioRecordNotFound("The Scenario suite was not found.")
        return ScenarioSuite.model_validate(_json_load(_row_value(row, "payload_json")))

    def list_scenario_suites(
        self,
        build_id: str,
        *,
        project_id: str,
        principal_key: str,
    ) -> list[ScenarioSuite]:
        self.get_build(
            build_id,
            project_id=project_id,
            principal_key=principal_key,
        )
        with self._reader() as connection:
            rows = self._execute(
                connection,
                """
                SELECT payload_json FROM studio_scenario_suites
                WHERE build_id = ? AND project_id = ?
                ORDER BY created_at DESC
                """,
                (build_id, project_id),
            ).fetchall()
        return [
            ScenarioSuite.model_validate(_json_load(_row_value(row, "payload_json")))
            for row in rows
        ]

    def create_scenario_file_fixture(
        self,
        fixture: ScenarioFileFixture,
        *,
        principal_key: str,
    ) -> ScenarioFileFixture:
        self.get_project_for_principal(fixture.project_id, principal_key)
        with self._transaction(immediate=True) as connection:
            self._execute(
                connection,
                """
                INSERT INTO studio_scenario_file_fixtures(
                    id, project_id, name, opaque_ref, media_type, size_bytes,
                    content_hash, approved_by, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fixture.id,
                    fixture.project_id,
                    fixture.name,
                    fixture.opaque_ref,
                    fixture.media_type,
                    fixture.size_bytes,
                    fixture.content_hash,
                    fixture.approved_by,
                    _timestamp(fixture.expires_at),
                    _timestamp(fixture.created_at),
                ),
            )
        return fixture

    def get_scenario_file_fixture(
        self,
        fixture_id: str,
        *,
        project_id: str,
        principal_key: str,
    ) -> ScenarioFileFixture:
        self.get_project_for_principal(project_id, principal_key)
        with self._reader() as connection:
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_scenario_file_fixtures
                WHERE id = ? AND project_id = ?
                """,
                (fixture_id, project_id),
            ).fetchone()
        if row is None:
            raise StudioRecordNotFound("The approved Scenario file fixture was not found.")
        return _scenario_file_fixture_from_row(row)

    def list_scenario_file_fixtures(
        self,
        *,
        project_id: str,
        principal_key: str,
        current_at: datetime | None = None,
    ) -> list[ScenarioFileFixture]:
        self.get_project_for_principal(project_id, principal_key)
        with self._reader() as connection:
            rows = self._execute(
                connection,
                """
                SELECT * FROM studio_scenario_file_fixtures
                WHERE project_id = ? AND expires_at > ?
                ORDER BY created_at DESC
                """,
                (project_id, _timestamp(current_at or utc_now())),
            ).fetchall()
        return [_scenario_file_fixture_from_row(row) for row in rows]

    def save_sanitized_run_source(
        self,
        approval: ScenarioSanitizedRunApproval,
        *,
        principal_key: str,
    ) -> ScenarioSanitizedRunApproval:
        self.get_project_for_principal(approval.project_id, principal_key)
        with self._transaction(immediate=True) as connection:
            self._execute(
                connection,
                """
                INSERT INTO studio_scenario_sanitized_run_sources(
                    id, project_id, source_run_id, evidence_hash,
                    approved_by, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, source_run_id, evidence_hash) DO NOTHING
                """,
                (
                    approval.id,
                    approval.project_id,
                    approval.source_run_id,
                    approval.evidence_hash,
                    approval.approved_by,
                    _timestamp(approval.expires_at),
                    _timestamp(approval.created_at),
                ),
            )
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_scenario_sanitized_run_sources
                WHERE project_id = ? AND source_run_id = ? AND evidence_hash = ?
                """,
                (
                    approval.project_id,
                    approval.source_run_id,
                    approval.evidence_hash,
                ),
            ).fetchone()
        assert row is not None
        return _scenario_sanitized_run_source_from_row(row)

    def get_sanitized_run_source(
        self,
        *,
        project_id: str,
        principal_key: str,
        source_run_id: str,
        evidence_hash: str,
    ) -> ScenarioSanitizedRunApproval:
        self.get_project_for_principal(project_id, principal_key)
        with self._reader() as connection:
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_scenario_sanitized_run_sources
                WHERE project_id = ? AND source_run_id = ? AND evidence_hash = ?
                """,
                (project_id, source_run_id, evidence_hash),
            ).fetchone()
        if row is None:
            raise StudioRecordNotFound(
                "The approved sanitized Run source was not found."
            )
        return _scenario_sanitized_run_source_from_row(row)

    def list_sanitized_run_sources(
        self,
        *,
        project_id: str,
        principal_key: str,
        current_at: datetime | None = None,
    ) -> list[ScenarioSanitizedRunApproval]:
        self.get_project_for_principal(project_id, principal_key)
        with self._reader() as connection:
            rows = self._execute(
                connection,
                """
                SELECT * FROM studio_scenario_sanitized_run_sources
                WHERE project_id = ? AND expires_at > ?
                ORDER BY created_at DESC
                """,
                (project_id, _timestamp(current_at or utc_now())),
            ).fetchall()
        return [_scenario_sanitized_run_source_from_row(row) for row in rows]

    def ensure_preview_environment(
        self,
        *,
        project_id: str,
        principal_key: str,
        target_key: str,
        name: str,
        enabled: bool,
        default_ttl_seconds: int,
    ) -> PreviewEnvironment:
        self.get_project_for_principal(project_id, principal_key)
        now = utc_now()
        environment_id = str(
            uuid5(NAMESPACE_URL, f"chat2dify:preview:{project_id}:{target_key}")
        )
        with self._transaction(immediate=True) as connection:
            self._execute(
                connection,
                """
                INSERT INTO studio_preview_environments(
                    id, project_id, target_key, name, enabled,
                    default_ttl_seconds, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, target_key) DO UPDATE SET
                    name = excluded.name,
                    enabled = excluded.enabled,
                    default_ttl_seconds = excluded.default_ttl_seconds,
                    updated_at = excluded.updated_at
                """,
                (
                    environment_id,
                    project_id,
                    target_key,
                    name,
                    int(enabled),
                    default_ttl_seconds,
                    _timestamp(now),
                    _timestamp(now),
                ),
            )
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_preview_environments
                WHERE project_id = ? AND target_key = ?
                """,
                (project_id, target_key),
            ).fetchone()
        assert row is not None
        return _preview_environment_from_row(row)

    def get_preview_environment(
        self,
        environment_id: str,
        *,
        project_id: str,
        principal_key: str,
    ) -> PreviewEnvironment:
        self.get_project_for_principal(project_id, principal_key)
        with self._reader() as connection:
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_preview_environments
                WHERE id = ? AND project_id = ?
                """,
                (environment_id, project_id),
            ).fetchone()
        if row is None:
            raise StudioRecordNotFound("The isolated Preview Environment was not found.")
        return _preview_environment_from_row(row)

    def create_scenario_run(
        self,
        run: ScenarioRun,
        *,
        principal_key: str,
    ) -> ScenarioRun:
        self.get_project_for_principal(run.project_id, principal_key)
        with self._transaction(immediate=True) as connection:
            self._execute(
                connection,
                """
                INSERT INTO studio_scenario_runs(
                    id, project_id, build_id, suite_id, environment_id,
                    candidate_ids_json, mappings_json, policy_json,
                    authorized_by, status, cancel_requested, reports_json,
                    comparison_json, failure_json, cleanup_verified, version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _scenario_run_values(run),
            )
            _insert_activity(
                self,
                connection,
                project_id=run.project_id,
                principal_key=principal_key,
                kind="scenario.run.created",
                entity_type="scenario_run",
                entity_id=run.id,
                summary={
                    "suite_id": run.suite_id,
                    "candidate_count": len(run.candidate_ids),
                    "status": run.status,
                },
                now=run.created_at,
            )
        return run

    def get_scenario_run(
        self,
        run_id: str,
        *,
        project_id: str,
        principal_key: str,
    ) -> ScenarioRun:
        self.get_project_for_principal(project_id, principal_key)
        with self._reader() as connection:
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_scenario_runs
                WHERE id = ? AND project_id = ?
                """,
                (run_id, project_id),
            ).fetchone()
        if row is None:
            raise StudioRecordNotFound("The Scenario Run was not found.")
        return _scenario_run_from_row(row)

    def list_scenario_runs(
        self,
        build_id: str,
        *,
        project_id: str,
        principal_key: str,
        limit: int = 20,
    ) -> list[ScenarioRun]:
        self.get_build(
            build_id,
            project_id=project_id,
            principal_key=principal_key,
        )
        with self._reader() as connection:
            rows = self._execute(
                connection,
                """
                SELECT * FROM studio_scenario_runs
                WHERE build_id = ? AND project_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (build_id, project_id, max(1, min(limit, 100))),
            ).fetchall()
        return [_scenario_run_from_row(row) for row in rows]

    def update_scenario_run(
        self,
        run: ScenarioRun,
        *,
        principal_key: str,
        expected_version: int,
    ) -> ScenarioRun:
        self.get_project_for_principal(run.project_id, principal_key)
        updated = run.model_copy(update={"version": expected_version + 1, "updated_at": utc_now()})
        with self._transaction(immediate=True) as connection:
            cursor = self._execute(
                connection,
                """
                UPDATE studio_scenario_runs SET
                    build_id = ?, suite_id = ?, environment_id = ?,
                    candidate_ids_json = ?, mappings_json = ?, policy_json = ?,
                    authorized_by = ?, status = ?, cancel_requested = ?,
                    reports_json = ?, comparison_json = ?, failure_json = ?,
                    cleanup_verified = ?, version = ?, updated_at = ?
                WHERE id = ? AND project_id = ? AND version = ?
                """,
                (
                    updated.build_id,
                    updated.suite_id,
                    updated.environment_id,
                    _json_dump(updated.candidate_ids),
                    _json_dump([item.model_dump(mode="json") for item in updated.mappings]),
                    _json_dump(updated.policy.model_dump(mode="json")),
                    updated.authorized_by,
                    updated.status,
                    int(updated.cancel_requested),
                    _json_dump([item.model_dump(mode="json") for item in updated.reports]),
                    _json_dump(updated.comparison.model_dump(mode="json")) if updated.comparison else None,
                    _json_dump(_safe_json(updated.failure)) if updated.failure else None,
                    int(updated.cleanup_verified),
                    updated.version,
                    _timestamp(updated.updated_at),
                    updated.id,
                    updated.project_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise StudioConflict("The Scenario Run changed while it was being updated.")
        return updated

    def request_scenario_run_cancel(
        self,
        run_id: str,
        *,
        project_id: str,
        principal_key: str,
    ) -> ScenarioRun:
        run = self.get_scenario_run(
            run_id,
            project_id=project_id,
            principal_key=principal_key,
        )
        if run.status not in {"pending", "running"}:
            return run
        return self.update_scenario_run(
            run.model_copy(update={"cancel_requested": True}),
            principal_key=principal_key,
            expected_version=run.version,
        )

    def interrupt_active_scenario_runs(self) -> int:
        """Persist restart state without replaying Preview or cleanup work."""
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            cursor = self._execute(
                connection,
                """
                UPDATE studio_scenario_runs
                SET status = 'interrupted',
                    failure_json = ?,
                    version = version + 1,
                    updated_at = ?
                WHERE status IN ('pending', 'running')
                """,
                (
                    _json_dump(
                        {
                            "code": "SCENARIO_RUN_INTERRUPTED",
                            "message": (
                                "The service restarted. Preview import, execution, and "
                                "cleanup were not replayed; reconcile fixtures explicitly."
                            ),
                        }
                    ),
                    _timestamp(now),
                ),
            )
        return int(cursor.rowcount)

    def create_preview_fixture(
        self,
        fixture: PreviewFixture,
        *,
        principal_key: str,
    ) -> PreviewFixture:
        self.get_project_for_principal(fixture.project_id, principal_key)
        with self._transaction(immediate=True) as connection:
            self._execute(
                connection,
                """
                INSERT INTO studio_preview_fixtures(
                    id, project_id, scenario_run_id, candidate_id,
                    environment_id, label, status, idempotency_key,
                    import_id, app_id, receipt_json, cleanup_attempts,
                    absence_verified_at, expires_at, version, created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _preview_fixture_values(fixture),
            )
        return fixture

    def get_preview_fixture(
        self,
        fixture_id: str,
        *,
        project_id: str,
        principal_key: str,
    ) -> PreviewFixture:
        self.get_project_for_principal(project_id, principal_key)
        with self._reader() as connection:
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_preview_fixtures
                WHERE id = ? AND project_id = ?
                """,
                (fixture_id, project_id),
            ).fetchone()
        if row is None:
            raise StudioRecordNotFound("The Preview fixture was not found.")
        return _preview_fixture_from_row(row)

    def list_preview_fixtures(
        self,
        *,
        project_id: str,
        principal_key: str,
        scenario_run_id: str | None = None,
        expired_before: datetime | None = None,
    ) -> list[PreviewFixture]:
        self.get_project_for_principal(project_id, principal_key)
        predicates = ["project_id = ?"]
        params: list[Any] = [project_id]
        if scenario_run_id is not None:
            predicates.append("scenario_run_id = ?")
            params.append(scenario_run_id)
        if expired_before is not None:
            predicates.append("expires_at <= ?")
            params.append(_timestamp(expired_before))
        with self._reader() as connection:
            rows = self._execute(
                connection,
                f"""
                SELECT * FROM studio_preview_fixtures
                WHERE {' AND '.join(predicates)}
                ORDER BY created_at ASC
                """,
                tuple(params),
            ).fetchall()
        return [_preview_fixture_from_row(row) for row in rows]

    def update_preview_fixture(
        self,
        fixture: PreviewFixture,
        *,
        principal_key: str,
        expected_version: int,
    ) -> PreviewFixture:
        self.get_project_for_principal(fixture.project_id, principal_key)
        updated = fixture.model_copy(update={"version": expected_version + 1, "updated_at": utc_now()})
        with self._transaction(immediate=True) as connection:
            cursor = self._execute(
                connection,
                """
                UPDATE studio_preview_fixtures SET
                    status = ?, import_id = ?, app_id = ?, receipt_json = ?,
                    cleanup_attempts = ?, absence_verified_at = ?, expires_at = ?,
                    version = ?, updated_at = ?
                WHERE id = ? AND project_id = ? AND version = ?
                """,
                (
                    updated.status,
                    updated.import_id,
                    updated.app_id,
                    _json_dump(_safe_json(updated.receipt)),
                    updated.cleanup_attempts,
                    _timestamp(updated.absence_verified_at) if updated.absence_verified_at else None,
                    _timestamp(updated.expires_at),
                    updated.version,
                    _timestamp(updated.updated_at),
                    updated.id,
                    updated.project_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise StudioConflict("The Preview fixture changed while it was being updated.")
        return updated

    def save_scenario_baseline(
        self,
        baseline: ScenarioBaseline,
        *,
        principal_key: str,
    ) -> ScenarioBaseline:
        self.get_project_for_principal(baseline.project_id, principal_key)
        with self._transaction(immediate=True) as connection:
            self._execute(
                connection,
                """
                INSERT INTO studio_scenario_baselines(
                    id, project_id, build_id, suite_id, report_run_id,
                    candidate_id, binding_json, report_hash, saved_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, build_id, suite_id) DO UPDATE SET
                    id = excluded.id,
                    report_run_id = excluded.report_run_id,
                    candidate_id = excluded.candidate_id,
                    binding_json = excluded.binding_json,
                    report_hash = excluded.report_hash,
                    saved_by = excluded.saved_by,
                    created_at = excluded.created_at
                """,
                (
                    baseline.id,
                    baseline.project_id,
                    baseline.build_id,
                    baseline.suite_id,
                    baseline.report_run_id,
                    baseline.candidate_id,
                    _json_dump(baseline.binding.model_dump(mode="json")),
                    baseline.report_hash,
                    baseline.saved_by,
                    _timestamp(baseline.created_at),
                ),
            )
        return baseline

    def get_scenario_baseline(
        self,
        build_id: str,
        *,
        project_id: str,
        principal_key: str,
        suite_id: str | None = None,
    ) -> ScenarioBaseline | None:
        self.get_build(
            build_id,
            project_id=project_id,
            principal_key=principal_key,
        )
        predicate = "build_id = ? AND project_id = ?"
        params: tuple[Any, ...] = (build_id, project_id)
        if suite_id is not None:
            predicate += " AND suite_id = ?"
            params = (*params, suite_id)
        with self._reader() as connection:
            row = self._execute(
                connection,
                f"""
                SELECT * FROM studio_scenario_baselines
                WHERE {predicate}
                ORDER BY created_at DESC LIMIT 1
                """,
                params,
            ).fetchone()
        return _scenario_baseline_from_row(row) if row is not None else None

    def upsert_regression_gate(
        self,
        gate: RegressionGate,
        *,
        principal_key: str,
    ) -> RegressionGate:
        self.get_project_for_principal(gate.project_id, principal_key)
        with self._transaction(immediate=True) as connection:
            self._execute(
                connection,
                """
                INSERT INTO studio_regression_gates(
                    id, project_id, build_id, suite_id, payload_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, build_id) DO UPDATE SET
                    id = excluded.id,
                    suite_id = excluded.suite_id,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    gate.id,
                    gate.project_id,
                    gate.build_id,
                    gate.suite_id,
                    _json_dump(gate.model_dump(mode="json")),
                    _timestamp(gate.created_at),
                    _timestamp(gate.updated_at),
                ),
            )
        return gate

    def get_regression_gate(
        self,
        build_id: str,
        *,
        project_id: str,
        principal_key: str,
    ) -> RegressionGate | None:
        self.get_build(
            build_id,
            project_id=project_id,
            principal_key=principal_key,
        )
        with self._reader() as connection:
            row = self._execute(
                connection,
                """
                SELECT payload_json FROM studio_regression_gates
                WHERE build_id = ? AND project_id = ?
                """,
                (build_id, project_id),
            ).fetchone()
        if row is None:
            return None
        return RegressionGate.model_validate(_json_load(_row_value(row, "payload_json")))

    def enqueue_job(
        self,
        *,
        project_id: str,
        principal_key: str,
        kind: str,
        payload: dict[str, Any],
        idempotency_key: str,
        max_attempts: int = 5,
    ) -> DurableJob:
        self.get_project_for_principal(project_id, principal_key)
        now = utc_now()
        job_id = new_id()
        with self._transaction(immediate=True) as connection:
            self._execute(
                connection,
                """
                INSERT INTO studio_jobs(
                    id, project_id, kind, payload_json, status, attempts,
                    max_attempts, lease_owner, lease_expires_at,
                    idempotency_key, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', 0, ?, NULL, NULL, ?, 1, ?, ?)
                ON CONFLICT(project_id, kind, idempotency_key) DO NOTHING
                """,
                (
                    job_id,
                    project_id,
                    kind,
                    _json_dump(_safe_json(payload)),
                    max_attempts,
                    idempotency_key,
                    _timestamp(now),
                    _timestamp(now),
                ),
            )
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_jobs
                WHERE project_id = ? AND kind = ? AND idempotency_key = ?
                """,
                (project_id, kind, idempotency_key),
            ).fetchone()
        assert row is not None
        return _job_from_row(row)

    def claim_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> DurableJob | None:
        now = utc_now()
        expires = datetime.fromtimestamp(
            _timestamp(now) + lease_seconds,
            tz=timezone.utc,
        )
        with self._transaction(immediate=True) as connection:
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_jobs
                WHERE attempts < max_attempts
                  AND (
                    status = 'pending'
                    OR (
                        status = 'leased'
                        AND lease_expires_at IS NOT NULL
                        AND lease_expires_at < ?
                    )
                  )
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (_timestamp(now),),
            ).fetchone()
            if row is None:
                return None
            job = _job_from_row(row)
            cursor = self._execute(
                connection,
                """
                UPDATE studio_jobs
                SET status = 'leased', attempts = attempts + 1,
                    lease_owner = ?, lease_expires_at = ?,
                    version = version + 1, updated_at = ?
                WHERE id = ? AND version = ?
                """,
                (
                    worker_id,
                    _timestamp(expires),
                    _timestamp(now),
                    job.id,
                    job.version,
                ),
            )
            if cursor.rowcount != 1:
                return None
            claimed = self._execute(
                connection,
                "SELECT * FROM studio_jobs WHERE id = ?",
                (job.id,),
            ).fetchone()
        assert claimed is not None
        return _job_from_row(claimed)

    def heartbeat_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        expected_version: int,
        lease_seconds: int,
    ) -> DurableJob:
        now = utc_now()
        expires = datetime.fromtimestamp(
            _timestamp(now) + lease_seconds,
            tz=timezone.utc,
        )
        with self._transaction(immediate=True) as connection:
            cursor = self._execute(
                connection,
                """
                UPDATE studio_jobs
                SET lease_expires_at = ?, version = version + 1, updated_at = ?
                WHERE id = ? AND status = 'leased' AND lease_owner = ?
                  AND version = ?
                """,
                (
                    _timestamp(expires),
                    _timestamp(now),
                    job_id,
                    worker_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise StudioConflict("The job lease changed or is no longer owned.")
            row = self._execute(
                connection,
                "SELECT * FROM studio_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        assert row is not None
        return _job_from_row(row)

    def finish_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        expected_version: int,
        outcome: str,
    ) -> DurableJob:
        if outcome not in {"completed", "failed", "ambiguous"}:
            raise ValueError("A job outcome must be completed, failed, or ambiguous.")
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            cursor = self._execute(
                connection,
                """
                UPDATE studio_jobs
                SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                    version = version + 1, updated_at = ?
                WHERE id = ? AND status = 'leased' AND lease_owner = ?
                  AND version = ?
                """,
                (
                    outcome,
                    _timestamp(now),
                    job_id,
                    worker_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise StudioConflict("The job lease changed or is no longer owned.")
            row = self._execute(
                connection,
                "SELECT * FROM studio_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        assert row is not None
        return _job_from_row(row)

    def enqueue_outbox(
        self,
        *,
        project_id: str,
        principal_key: str,
        topic: str,
        payload: dict[str, Any],
        idempotency_key: str,
        max_attempts: int = 5,
    ) -> OutboxMessage:
        self.get_project_for_principal(project_id, principal_key)
        now = utc_now()
        message_id = new_id()
        with self._transaction(immediate=True) as connection:
            self._execute(
                connection,
                """
                INSERT INTO studio_outbox(
                    id, project_id, topic, payload_json, status, attempts,
                    max_attempts, lease_owner, lease_expires_at,
                    idempotency_key, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', 0, ?, NULL, NULL, ?, 1, ?, ?)
                ON CONFLICT(project_id, topic, idempotency_key) DO NOTHING
                """,
                (
                    message_id,
                    project_id,
                    topic,
                    _json_dump(_safe_json(payload)),
                    max_attempts,
                    idempotency_key,
                    _timestamp(now),
                    _timestamp(now),
                ),
            )
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_outbox
                WHERE project_id = ? AND topic = ? AND idempotency_key = ?
                """,
                (project_id, topic, idempotency_key),
            ).fetchone()
        assert row is not None
        return _outbox_from_row(row)

    def claim_outbox(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> OutboxMessage | None:
        now = utc_now()
        expires = datetime.fromtimestamp(
            _timestamp(now) + lease_seconds,
            tz=timezone.utc,
        )
        with self._transaction(immediate=True) as connection:
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_outbox
                WHERE attempts < max_attempts
                  AND (
                    status = 'pending'
                    OR (
                        status = 'leased'
                        AND lease_expires_at IS NOT NULL
                        AND lease_expires_at < ?
                    )
                  )
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (_timestamp(now),),
            ).fetchone()
            if row is None:
                return None
            message = _outbox_from_row(row)
            cursor = self._execute(
                connection,
                """
                UPDATE studio_outbox
                SET status = 'leased', attempts = attempts + 1,
                    lease_owner = ?, lease_expires_at = ?,
                    version = version + 1, updated_at = ?
                WHERE id = ? AND version = ?
                """,
                (
                    worker_id,
                    _timestamp(expires),
                    _timestamp(now),
                    message.id,
                    message.version,
                ),
            )
            if cursor.rowcount != 1:
                return None
            claimed = self._execute(
                connection,
                "SELECT * FROM studio_outbox WHERE id = ?",
                (message.id,),
            ).fetchone()
        assert claimed is not None
        return _outbox_from_row(claimed)

    def finish_outbox(
        self,
        *,
        message_id: str,
        worker_id: str,
        expected_version: int,
        outcome: str,
    ) -> OutboxMessage:
        if outcome not in {"completed", "failed", "ambiguous"}:
            raise ValueError(
                "An outbox outcome must be completed, failed, or ambiguous."
            )
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            cursor = self._execute(
                connection,
                """
                UPDATE studio_outbox
                SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                    version = version + 1, updated_at = ?
                WHERE id = ? AND status = 'leased' AND lease_owner = ?
                  AND version = ?
                """,
                (
                    outcome,
                    _timestamp(now),
                    message_id,
                    worker_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise StudioConflict(
                    "The outbox lease changed or is no longer owned."
                )
            row = self._execute(
                connection,
                "SELECT * FROM studio_outbox WHERE id = ?",
                (message_id,),
            ).fetchone()
        assert row is not None
        return _outbox_from_row(row)

    def record_receipt(
        self,
        *,
        project_id: str,
        principal_key: str,
        operation: str,
        idempotency_key: str,
        outcome: str,
        external_ref: str | None,
        details: dict[str, Any],
    ) -> ExternalReceipt:
        self.get_project_for_principal(project_id, principal_key)
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            existing_row = self._execute(
                connection,
                """
                SELECT * FROM studio_receipts
                WHERE project_id = ? AND operation = ? AND idempotency_key = ?
                """,
                (project_id, operation, idempotency_key),
            ).fetchone()
            if existing_row is None:
                self._execute(
                    connection,
                    """
                    INSERT INTO studio_receipts(
                        id, project_id, operation, idempotency_key, outcome,
                        external_ref, details_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id(),
                        project_id,
                        operation,
                        idempotency_key,
                        outcome,
                        external_ref,
                        _json_dump(_safe_json(details)),
                        _timestamp(now),
                    ),
                )
            else:
                existing = _receipt_from_row(existing_row)
                if existing.outcome == "pending" and outcome != "pending":
                    self._execute(
                        connection,
                        """
                        UPDATE studio_receipts
                        SET outcome = ?, external_ref = ?, details_json = ?
                        WHERE id = ?
                        """,
                        (
                            outcome,
                            external_ref,
                            _json_dump(_safe_json(details)),
                            existing.id,
                        ),
                    )
            row = self._execute(
                connection,
                """
                SELECT * FROM studio_receipts
                WHERE project_id = ? AND operation = ? AND idempotency_key = ?
                """,
                (project_id, operation, idempotency_key),
            ).fetchone()
        assert row is not None
        receipt = _receipt_from_row(row)
        if receipt.outcome != outcome or receipt.external_ref != external_ref:
            raise StudioConflict(
                "An external receipt already exists for this idempotency key."
            )
        return receipt

    def list_receipts(
        self,
        *,
        project_id: str,
        principal_key: str,
        operation_prefix: str | None = None,
    ) -> list[ExternalReceipt]:
        self.get_project_for_principal(project_id, principal_key)
        with self._reader() as connection:
            if operation_prefix is None:
                rows = self._execute(
                    connection,
                    """
                    SELECT * FROM studio_receipts
                    WHERE project_id = ?
                    ORDER BY created_at ASC, id ASC
                    """,
                    (project_id,),
                ).fetchall()
            else:
                rows = self._execute(
                    connection,
                    """
                    SELECT * FROM studio_receipts
                    WHERE project_id = ? AND operation LIKE ?
                    ORDER BY created_at ASC, id ASC
                    """,
                    (project_id, f"{operation_prefix}%"),
                ).fetchall()
        return [_receipt_from_row(row) for row in rows]


def _insert_activity(
    store: StudioStore,
    connection: Any,
    *,
    project_id: str,
    principal_key: str,
    kind: str,
    entity_type: str,
    entity_id: str,
    summary: dict[str, Any],
    now: datetime,
    activity_id: str | None = None,
) -> None:
    store._execute(
        connection,
        """
        INSERT INTO studio_activity(
            id, project_id, principal_key, kind, entity_type, entity_id,
            summary_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            activity_id or new_id(),
            project_id,
            principal_key,
            kind,
            entity_type,
            entity_id,
            _json_dump(_safe_json(summary)),
            _timestamp(now),
        ),
    )


def _project_from_row(row: Any) -> Project:
    return Project(
        id=str(_row_value(row, "id")),
        slug=str(_row_value(row, "slug")),
        name=str(_row_value(row, "name")),
        kind=str(_row_value(row, "kind")),
        dify_tenant_id=str(_row_value(row, "dify_tenant_id")),
        version=int(_row_value(row, "version")),
        created_at=_datetime(_row_value(row, "created_at")),
        updated_at=_datetime(_row_value(row, "updated_at")),
    )


def _membership_from_row(row: Any) -> Membership:
    return Membership(
        id=str(_row_value(row, "id")),
        project_id=str(_row_value(row, "project_id")),
        principal_key=str(_row_value(row, "principal_key")),
        role=str(_row_value(row, "role")),
        version=int(_row_value(row, "version")),
        created_at=_datetime(_row_value(row, "created_at")),
        updated_at=_datetime(_row_value(row, "updated_at")),
    )


def _project_from_joined_row(row: Any) -> Project:
    return Project(
        id=str(_row_value(row, "p_id")),
        slug=str(_row_value(row, "p_slug")),
        name=str(_row_value(row, "p_name")),
        kind=str(_row_value(row, "p_kind")),
        dify_tenant_id=str(_row_value(row, "p_dify_tenant_id")),
        version=int(_row_value(row, "p_version")),
        created_at=_datetime(_row_value(row, "p_created_at")),
        updated_at=_datetime(_row_value(row, "p_updated_at")),
    )


def _membership_from_joined_row(row: Any) -> Membership:
    return Membership(
        id=str(_row_value(row, "m_id")),
        project_id=str(_row_value(row, "m_project_id")),
        principal_key=str(_row_value(row, "m_principal_key")),
        role=str(_row_value(row, "m_role")),
        version=int(_row_value(row, "m_version")),
        created_at=_datetime(_row_value(row, "m_created_at")),
        updated_at=_datetime(_row_value(row, "m_updated_at")),
    )


def _session_from_row(row: Any) -> StudioSession:
    return StudioSession(
        id=str(_row_value(row, "id")),
        jti_hash=str(_row_value(row, "jti_hash")),
        principal_key=str(_row_value(row, "principal_key")),
        project_id=str(_row_value(row, "project_id")),
        dify_account_id=str(_row_value(row, "dify_account_id")),
        dify_tenant_id=str(_row_value(row, "dify_tenant_id")),
        origin=str(_row_value(row, "origin")),
        nonce_hash=str(_row_value(row, "nonce_hash")),
        expires_at=_datetime(_row_value(row, "expires_at")),
        created_at=_datetime(_row_value(row, "created_at")),
        revoked_at=_optional_datetime(_row_value(row, "revoked_at")),
    )


def _activity_from_row(row: Any) -> Activity:
    return Activity(
        id=str(_row_value(row, "id")),
        project_id=str(_row_value(row, "project_id")),
        principal_key=str(_row_value(row, "principal_key")),
        kind=str(_row_value(row, "kind")),
        entity_type=str(_row_value(row, "entity_type")),
        entity_id=str(_row_value(row, "entity_id")),
        summary=_json_load(_row_value(row, "summary_json")),
        created_at=_datetime(_row_value(row, "created_at")),
    )


def _build_from_row(row: Any) -> StudioBuild:
    return StudioBuild(
        id=str(_row_value(row, "id")),
        project_id=str(_row_value(row, "project_id")),
        created_by=str(_row_value(row, "created_by")),
        operation=str(_row_value(row, "operation")),
        entry_source=str(_row_value(row, "entry_source")),
        app_id=_optional_string(_row_value(row, "app_id")),
        app_mode=str(_row_value(row, "app_mode")),
        app_name=str(_row_value(row, "app_name")),
        base_fingerprint=_optional_string(_row_value(row, "base_fingerprint")),
        selected_candidate_id=_optional_string(
            _row_value(row, "selected_candidate_id")
        ),
        status=str(_row_value(row, "status")),
        version=int(_row_value(row, "version")),
        created_at=_datetime(_row_value(row, "created_at")),
        updated_at=_datetime(_row_value(row, "updated_at")),
    )


def _candidate_from_row(row: Any) -> StudioCandidate:
    source_payload = _json_load(_row_value(row, "source_candidate_ids_json"))
    raw_sources = source_payload.get("ids")
    return StudioCandidate(
        id=str(_row_value(row, "id")),
        project_id=str(_row_value(row, "project_id")),
        build_id=str(_row_value(row, "build_id")),
        run_id=str(_row_value(row, "run_id")),
        label=str(_row_value(row, "label")),
        intent=str(_row_value(row, "intent")),
        source_candidate_ids=(
            [str(item) for item in raw_sources]
            if isinstance(raw_sources, list)
            else []
        ),
        base_fingerprint=_optional_string(_row_value(row, "base_fingerprint")),
        status=str(_row_value(row, "status")),
        ordinal=int(_row_value(row, "ordinal")),
        version=int(_row_value(row, "version")),
        created_at=_datetime(_row_value(row, "created_at")),
        updated_at=_datetime(_row_value(row, "updated_at")),
    )


def _blueprint_version_from_row(row: Any) -> BlueprintVersionRecord:
    definition = BlueprintDefinition.model_validate(
        _json_load(_row_value(row, "definition_json"))
    )
    return BlueprintVersionRecord(
        id=str(_row_value(row, "id")),
        blueprint_id=str(_row_value(row, "blueprint_id")),
        project_id=_optional_string(_row_value(row, "project_id")),
        version=str(_row_value(row, "semantic_version")),
        status=str(_row_value(row, "status")),
        definition=definition,
        created_by=str(_row_value(row, "created_by")),
        reviewed_by=_optional_string(_row_value(row, "reviewed_by")),
        review_note=_optional_string(_row_value(row, "review_note")),
        created_at=_datetime(_row_value(row, "created_at")),
        reviewed_at=_optional_datetime(_row_value(row, "reviewed_at")),
    )


def _blueprint_template_from_row(row: Any) -> dict[str, Any]:
    payload = _json_load(_row_value(row, "template_json"))
    return payload if isinstance(payload, dict) else {}


def _blueprint_application_from_row(row: Any) -> BlueprintApplication:
    return BlueprintApplication(
        id=str(_row_value(row, "id")),
        project_id=str(_row_value(row, "project_id")),
        build_id=str(_row_value(row, "build_id")),
        candidate_id=str(_row_value(row, "candidate_id")),
        blueprint_id=str(_row_value(row, "blueprint_id")),
        blueprint_version=str(_row_value(row, "blueprint_version")),
        setup_hash=str(_row_value(row, "setup_hash")),
        applied_by=str(_row_value(row, "applied_by")),
        applied_at=_datetime(_row_value(row, "applied_at")),
    )


def _scenario_file_fixture_from_row(row: Any) -> ScenarioFileFixture:
    return ScenarioFileFixture(
        id=str(_row_value(row, "id")),
        project_id=str(_row_value(row, "project_id")),
        name=str(_row_value(row, "name")),
        opaque_ref=str(_row_value(row, "opaque_ref")),
        media_type=str(_row_value(row, "media_type")),
        size_bytes=int(_row_value(row, "size_bytes")),
        content_hash=str(_row_value(row, "content_hash")),
        approved_by=str(_row_value(row, "approved_by")),
        expires_at=_datetime(_row_value(row, "expires_at")),
        created_at=_datetime(_row_value(row, "created_at")),
    )


def _scenario_sanitized_run_source_from_row(
    row: Any,
) -> ScenarioSanitizedRunApproval:
    return ScenarioSanitizedRunApproval(
        id=str(_row_value(row, "id")),
        project_id=str(_row_value(row, "project_id")),
        source_run_id=str(_row_value(row, "source_run_id")),
        evidence_hash=str(_row_value(row, "evidence_hash")),
        approved_by=str(_row_value(row, "approved_by")),
        expires_at=_datetime(_row_value(row, "expires_at")),
        created_at=_datetime(_row_value(row, "created_at")),
    )


def _preview_environment_from_row(row: Any) -> PreviewEnvironment:
    return PreviewEnvironment(
        id=str(_row_value(row, "id")),
        project_id=str(_row_value(row, "project_id")),
        target_key=str(_row_value(row, "target_key")),
        name=str(_row_value(row, "name")),
        enabled=bool(_row_value(row, "enabled")),
        default_ttl_seconds=int(_row_value(row, "default_ttl_seconds")),
        created_at=_datetime(_row_value(row, "created_at")),
        updated_at=_datetime(_row_value(row, "updated_at")),
    )


def _scenario_run_values(run: ScenarioRun) -> tuple[Any, ...]:
    return (
        run.id,
        run.project_id,
        run.build_id,
        run.suite_id,
        run.environment_id,
        _json_dump(run.candidate_ids),
        _json_dump([item.model_dump(mode="json") for item in run.mappings]),
        _json_dump(run.policy.model_dump(mode="json")),
        run.authorized_by,
        run.status,
        int(run.cancel_requested),
        _json_dump([item.model_dump(mode="json") for item in run.reports]),
        _json_dump(run.comparison.model_dump(mode="json")) if run.comparison else None,
        _json_dump(_safe_json(run.failure)) if run.failure else None,
        int(run.cleanup_verified),
        run.version,
        _timestamp(run.created_at),
        _timestamp(run.updated_at),
    )


def _scenario_run_from_row(row: Any) -> ScenarioRun:
    return ScenarioRun.model_validate(
        {
            "id": str(_row_value(row, "id")),
            "project_id": str(_row_value(row, "project_id")),
            "build_id": str(_row_value(row, "build_id")),
            "suite_id": str(_row_value(row, "suite_id")),
            "environment_id": str(_row_value(row, "environment_id")),
            "candidate_ids": _json_value(_row_value(row, "candidate_ids_json")),
            "mappings": _json_value(_row_value(row, "mappings_json")),
            "policy": _json_load(_row_value(row, "policy_json")),
            "authorized_by": str(_row_value(row, "authorized_by")),
            "status": str(_row_value(row, "status")),
            "cancel_requested": bool(_row_value(row, "cancel_requested")),
            "reports": _json_value(_row_value(row, "reports_json")),
            "comparison": (
                _json_load(_row_value(row, "comparison_json"))
                if _row_value(row, "comparison_json") is not None
                else None
            ),
            "failure": (
                _json_load(_row_value(row, "failure_json"))
                if _row_value(row, "failure_json") is not None
                else None
            ),
            "cleanup_verified": bool(_row_value(row, "cleanup_verified")),
            "version": int(_row_value(row, "version")),
            "created_at": _datetime(_row_value(row, "created_at")),
            "updated_at": _datetime(_row_value(row, "updated_at")),
        }
    )


def _preview_fixture_values(fixture: PreviewFixture) -> tuple[Any, ...]:
    return (
        fixture.id,
        fixture.project_id,
        fixture.scenario_run_id,
        fixture.candidate_id,
        fixture.environment_id,
        fixture.label,
        fixture.status,
        fixture.idempotency_key,
        fixture.import_id,
        fixture.app_id,
        _json_dump(_safe_json(fixture.receipt)),
        fixture.cleanup_attempts,
        _timestamp(fixture.absence_verified_at) if fixture.absence_verified_at else None,
        _timestamp(fixture.expires_at),
        fixture.version,
        _timestamp(fixture.created_at),
        _timestamp(fixture.updated_at),
    )


def _preview_fixture_from_row(row: Any) -> PreviewFixture:
    return PreviewFixture(
        id=str(_row_value(row, "id")),
        project_id=str(_row_value(row, "project_id")),
        scenario_run_id=str(_row_value(row, "scenario_run_id")),
        candidate_id=str(_row_value(row, "candidate_id")),
        environment_id=str(_row_value(row, "environment_id")),
        label=str(_row_value(row, "label")),
        status=str(_row_value(row, "status")),
        idempotency_key=str(_row_value(row, "idempotency_key")),
        import_id=_optional_string(_row_value(row, "import_id")),
        app_id=_optional_string(_row_value(row, "app_id")),
        receipt=_json_load(_row_value(row, "receipt_json")),
        cleanup_attempts=int(_row_value(row, "cleanup_attempts")),
        absence_verified_at=_optional_datetime(_row_value(row, "absence_verified_at")),
        expires_at=_datetime(_row_value(row, "expires_at")),
        version=int(_row_value(row, "version")),
        created_at=_datetime(_row_value(row, "created_at")),
        updated_at=_datetime(_row_value(row, "updated_at")),
    )


def _scenario_baseline_from_row(row: Any) -> ScenarioBaseline:
    return ScenarioBaseline(
        id=str(_row_value(row, "id")),
        project_id=str(_row_value(row, "project_id")),
        build_id=str(_row_value(row, "build_id")),
        suite_id=str(_row_value(row, "suite_id")),
        report_run_id=str(_row_value(row, "report_run_id")),
        candidate_id=str(_row_value(row, "candidate_id")),
        binding=ScenarioEvidenceBinding.model_validate(
            _json_load(_row_value(row, "binding_json"))
        ),
        report_hash=str(_row_value(row, "report_hash")),
        saved_by=str(_row_value(row, "saved_by")),
        created_at=_datetime(_row_value(row, "created_at")),
    )


def _job_from_row(row: Any) -> DurableJob:
    return DurableJob(
        id=str(_row_value(row, "id")),
        project_id=str(_row_value(row, "project_id")),
        kind=str(_row_value(row, "kind")),
        payload=_json_load(_row_value(row, "payload_json")),
        status=str(_row_value(row, "status")),
        attempts=int(_row_value(row, "attempts")),
        max_attempts=int(_row_value(row, "max_attempts")),
        lease_owner=_optional_string(_row_value(row, "lease_owner")),
        lease_expires_at=_optional_datetime(_row_value(row, "lease_expires_at")),
        idempotency_key=str(_row_value(row, "idempotency_key")),
        version=int(_row_value(row, "version")),
        created_at=_datetime(_row_value(row, "created_at")),
        updated_at=_datetime(_row_value(row, "updated_at")),
    )


def _receipt_from_row(row: Any) -> ExternalReceipt:
    return ExternalReceipt(
        id=str(_row_value(row, "id")),
        project_id=str(_row_value(row, "project_id")),
        operation=str(_row_value(row, "operation")),
        idempotency_key=str(_row_value(row, "idempotency_key")),
        outcome=str(_row_value(row, "outcome")),
        external_ref=_optional_string(_row_value(row, "external_ref")),
        details=_json_load(_row_value(row, "details_json")),
        created_at=_datetime(_row_value(row, "created_at")),
    )


def _outbox_from_row(row: Any) -> OutboxMessage:
    return OutboxMessage(
        id=str(_row_value(row, "id")),
        project_id=str(_row_value(row, "project_id")),
        topic=str(_row_value(row, "topic")),
        payload=_json_load(_row_value(row, "payload_json")),
        status=str(_row_value(row, "status")),
        attempts=int(_row_value(row, "attempts")),
        max_attempts=int(_row_value(row, "max_attempts")),
        lease_owner=_optional_string(_row_value(row, "lease_owner")),
        lease_expires_at=_optional_datetime(_row_value(row, "lease_expires_at")),
        idempotency_key=str(_row_value(row, "idempotency_key")),
        version=int(_row_value(row, "version")),
        created_at=_datetime(_row_value(row, "created_at")),
        updated_at=_datetime(_row_value(row, "updated_at")),
    )


def _row_value(row: Any, key: str) -> Any:
    return row[key]


def _timestamp(value: datetime) -> float:
    return value.timestamp()


def _datetime(value: Any) -> datetime:
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _optional_datetime(value: Any) -> datetime | None:
    return None if value is None else _datetime(value)


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_load(value: Any) -> dict[str, Any]:
    parsed = json.loads(str(value))
    return parsed if isinstance(parsed, dict) else {}


def _json_value(value: Any) -> Any:
    return json.loads(str(value))


def _safe_json(value: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_sensitive_data(value)
    return redacted if isinstance(redacted, dict) else {}


def _is_unique_violation(exc: Exception) -> bool:
    if isinstance(exc, sqlite3.IntegrityError):
        return "UNIQUE constraint failed" in str(exc)
    return getattr(exc, "sqlstate", None) == "23505"
