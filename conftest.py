"""Real Postgres for the storage tests (shared by both packages).

Set TEST_DATABASE_URL to use an existing server. Otherwise an embedded server is
started via `pgserver` (pip-installed Postgres binaries, no Docker needed); without
it the Postgres tests are skipped. Each test gets a fresh schema.
"""

from __future__ import annotations

import os
import uuid

import pytest


@pytest.fixture(scope="session")
def pg_url(tmp_path_factory):
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return
    pgserver = pytest.importorskip("pgserver")
    server = pgserver.get_server(tmp_path_factory.mktemp("pg"), cleanup_mode="stop")
    yield server.get_uri()


@pytest.fixture
def pg(pg_url):
    """A connection whose search_path points at a brand-new, empty schema."""
    psycopg = pytest.importorskip("psycopg")
    schema = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(pg_url, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f'SET search_path TO "{schema}"')
        try:
            yield conn
        finally:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
