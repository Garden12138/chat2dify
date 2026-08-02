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
    CandidateStatus,
    DurableJob,
    ExternalReceipt,
    Membership,
    OutboxMessage,
    Principal,
    Project,
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


_MIGRATION_VERSION = 2
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
            self._execute(
                connection,
                """
                INSERT INTO studio_receipts(
                    id, project_id, operation, idempotency_key, outcome,
                    external_ref, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, operation, idempotency_key) DO NOTHING
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


def _safe_json(value: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_sensitive_data(value)
    return redacted if isinstance(redacted, dict) else {}


def _is_unique_violation(exc: Exception) -> bool:
    if isinstance(exc, sqlite3.IntegrityError):
        return "UNIQUE constraint failed" in str(exc)
    return getattr(exc, "sqlstate", None) == "23505"
