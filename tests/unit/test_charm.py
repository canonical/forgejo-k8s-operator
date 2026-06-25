# To learn more about testing, see https://ops.readthedocs.io/en/latest/explanation/testing.html

import json

import pytest
from charms.data_platform_libs.v0.data_interfaces import DatabaseRequires
from ops import pebble, testing

from charm import SERVICE_NAME
from charm import ForgejoK8SOperatorCharm as CharmForgejoCharm

CHECK_NAME = "service-ready"  # Name of Pebble check in the mock workload container.

layer = pebble.Layer(
    {
        "services": {
            SERVICE_NAME: {
                "override": "replace",
                "command": "/bin/foo",  # The specific command isn't important for unit tests.
                "startup": "enabled",
            }
        },
        "checks": {
            CHECK_NAME: {
                "override": "replace",
                "level": "ready",
                "threshold": 3,
                "startup": "enabled",
                "http": {
                    "url": "http://localhost:8000/version",  # The specific URL isn't important.
                },
            }
        },
    }
)


def mock_get_version():
    """Get a mock version string without executing the workload code."""
    return "1.0.0"


def test_pebble_ready(monkeypatch: pytest.MonkeyPatch):
    """Test that the charm has the correct state after handling the pebble-ready event."""
    # Arrange:
    ctx = testing.Context(CharmForgejoCharm)
    check_in = testing.CheckInfo(
        CHECK_NAME,
        level=pebble.CheckLevel.READY,
        status=pebble.CheckStatus.UP,  # Simulate the Pebble check passing.
    )
    container_in = testing.Container(
        "forgejo",
        can_connect=True,
        layers={"base": layer},
        service_statuses={SERVICE_NAME: pebble.ServiceStatus.INACTIVE},
        check_infos={check_in},
    )
    state_in = testing.State(containers={container_in})
    monkeypatch.setattr(
        CharmForgejoCharm, "_forgejo_version", property(lambda self: mock_get_version())
    )

    # Act:
    state_out = ctx.run(ctx.on.pebble_ready(container_in), state_in)

    # Assert:
    container_out = state_out.get_container(container_in.name)
    assert container_out.service_statuses[SERVICE_NAME] == pebble.ServiceStatus.ACTIVE
    assert state_out.workload_version is not None
    # The charm requires a database relation to reach ActiveStatus; without it
    # collect-status reports BlockedStatus, which is correct charm behaviour.
    assert state_out.unit_status == testing.BlockedStatus("Add a database relation")


def test_pebble_ready_service_not_ready():
    """Test that the charm raises an error if the workload isn't ready after Pebble starts it."""
    # Arrange:
    ctx = testing.Context(CharmForgejoCharm)
    check_in = testing.CheckInfo(
        CHECK_NAME,
        level=pebble.CheckLevel.READY,
        status=pebble.CheckStatus.DOWN,  # Simulate the Pebble check failing.
    )
    container_in = testing.Container(
        "forgejo",
        can_connect=True,
        layers={"base": layer},
        service_statuses={SERVICE_NAME: pebble.ServiceStatus.INACTIVE},
        check_infos={check_in},
    )
    state_in = testing.State(containers={container_in})

    # Act & assert:
    with pytest.raises(testing.errors.UncaughtCharmError):
        ctx.run(ctx.on.pebble_ready(container_in), state_in)


def test_config_propagates_to_env_vars(monkeypatch: pytest.MonkeyPatch):
    """Test that Juju config values are mapped to env vars and ini file."""
    ctx = testing.Context(CharmForgejoCharm)
    container_in = testing.Container(
        "forgejo",
        can_connect=True,
        layers={"base": layer},
        service_statuses={SERVICE_NAME: pebble.ServiceStatus.INACTIVE},
    )
    state_in = testing.State(
        containers={container_in},
        config={
            "forgejo__log__level": "Debug",
            "forgejo__repository__pull_request__default_merge_style": "rebase",
        },
    )
    monkeypatch.setattr(
        CharmForgejoCharm, "_forgejo_version", property(lambda self: mock_get_version())
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)
    # Assert: env vars appear in Pebble plan
    env = state_out.get_container("forgejo").plan.services[SERVICE_NAME].environment
    # Standard mapping
    assert env.get("FORGEJO__LOG__LEVEL") == "Debug"
    # Override mapping: dot in Forgejo section name encoded as _0X2E_
    assert env.get("FORGEJO__REPOSITORY_0X2E_PULL-REQUEST__DEFAULT_MERGE_STYLE") == "rebase"


def test_metrics_scrape_jobs_no_token(monkeypatch: pytest.MonkeyPatch):
    """Scrape jobs contain no authorization when no metrics token is configured."""
    ctx = testing.Context(CharmForgejoCharm)
    container_in = testing.Container("forgejo", can_connect=True)
    metrics_relation = testing.Relation("metrics-endpoint")
    state_in = testing.State(
        containers={container_in},
        relations={metrics_relation},
        leader=True,
    )
    monkeypatch.setattr(
        CharmForgejoCharm, "_forgejo_version", property(lambda self: mock_get_version())
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    rel_out = state_out.get_relation(metrics_relation.id)
    scrape_jobs = json.loads(rel_out.local_app_data.get("scrape_jobs", "[]"))
    assert len(scrape_jobs) == 1
    assert "authorization" not in scrape_jobs[0]


def test_metrics_scrape_jobs_with_token(monkeypatch: pytest.MonkeyPatch):
    """Scrape jobs include bearer-token authorization when the token secret is configured."""
    ctx = testing.Context(CharmForgejoCharm)
    container_in = testing.Container("forgejo", can_connect=True)
    metrics_relation = testing.Relation("metrics-endpoint")
    secret = testing.Secret(tracked_content={"value": "my-metrics-token"})
    state_in = testing.State(
        containers={container_in},
        relations={metrics_relation},
        secrets={secret},
        config={"forgejo__metrics__token": secret.id},
        leader=True,
    )
    monkeypatch.setattr(
        CharmForgejoCharm, "_forgejo_version", property(lambda self: mock_get_version())
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    rel_out = state_out.get_relation(metrics_relation.id)
    scrape_jobs = json.loads(rel_out.local_app_data.get("scrape_jobs", "[]"))
    assert len(scrape_jobs) == 1
    assert scrape_jobs[0].get("authorization") == {"credentials": "my-metrics-token"}


def test_secret_changed_triggers_reconcile(monkeypatch: pytest.MonkeyPatch):
    """A secret_changed event causes the charm to reconcile and pick up new secret content."""
    ctx = testing.Context(CharmForgejoCharm)
    container_in = testing.Container(
        "forgejo",
        can_connect=True,
        layers={"base": layer},
        service_statuses={SERVICE_NAME: pebble.ServiceStatus.INACTIVE},
    )
    secret = testing.Secret(
        tracked_content={"value": "old-key"},
        latest_content={"value": "new-rotated-key"},
    )
    state_in = testing.State(
        containers={container_in},
        secrets={secret},
        config={"forgejo__security__secret_key": secret.id},
    )
    monkeypatch.setattr(
        CharmForgejoCharm, "_forgejo_version", property(lambda self: mock_get_version())
    )

    state_out = ctx.run(ctx.on.secret_changed(secret=secret), state_in)
    env = state_out.get_container("forgejo").plan.services[SERVICE_NAME].environment
    # After secret_changed, get_content(refresh=True) returns the latest revision
    assert env.get("FORGEJO__SECURITY__SECRET_KEY") == "new-rotated-key"


_MOCK_DB_DATA = {
    1: {"endpoints": "host:5432", "username": "user", "password": "pass"},
}


def test_database_name_plain_when_exec_mode_unset(monkeypatch: pytest.MonkeyPatch):
    """FORGEJO__DATABASE__NAME is the plain database name when exec mode config is empty."""
    ctx = testing.Context(CharmForgejoCharm)
    container_in = testing.Container(
        "forgejo",
        can_connect=True,
        layers={"base": layer},
        service_statuses={SERVICE_NAME: pebble.ServiceStatus.INACTIVE},
    )
    state_in = testing.State(containers={container_in})
    monkeypatch.setattr(
        CharmForgejoCharm, "_forgejo_version", property(lambda self: mock_get_version())
    )
    monkeypatch.setattr(DatabaseRequires, "fetch_relation_data", lambda self, **kw: _MOCK_DB_DATA)

    state_out = ctx.run(ctx.on.config_changed(), state_in)
    env = state_out.get_container("forgejo").plan.services[SERVICE_NAME].environment
    db_name = env.get("FORGEJO__DATABASE__NAME")

    assert isinstance(db_name, str), "DATABASE NAME must be a string, not a tuple"
    assert "?" not in db_name, "No query parameters expected when exec mode is not configured"


def test_database_name_includes_exec_mode_when_configured(monkeypatch: pytest.MonkeyPatch):
    """FORGEJO__DATABASE__NAME includes ?default_query_exec_mode when config is set."""
    ctx = testing.Context(CharmForgejoCharm)
    container_in = testing.Container(
        "forgejo",
        can_connect=True,
        layers={"base": layer},
        service_statuses={SERVICE_NAME: pebble.ServiceStatus.INACTIVE},
    )
    state_in = testing.State(
        containers={container_in},
        config={"database-default-query-exec-mode": "cache_describe"},
    )
    monkeypatch.setattr(
        CharmForgejoCharm, "_forgejo_version", property(lambda self: mock_get_version())
    )
    monkeypatch.setattr(DatabaseRequires, "fetch_relation_data", lambda self, **kw: _MOCK_DB_DATA)

    state_out = ctx.run(ctx.on.config_changed(), state_in)
    env = state_out.get_container("forgejo").plan.services[SERVICE_NAME].environment
    db_name = env.get("FORGEJO__DATABASE__NAME")

    assert isinstance(db_name, str), "DATABASE NAME must be a string, not a tuple"
    assert db_name.endswith("?default_query_exec_mode=cache_describe"), (
        f"Expected pgx exec mode parameter in DATABASE NAME, got: {db_name!r}"
    )
