import pytest

from config import ForgejoConfig

VALID_KWARGS = {
    "forgejo__log__level": "Info",
    "forgejo__server__domain": "example.com",
    "forgejo__service__default_user_visibility": "public",
    "forgejo__service__default_org_visibility": "public",
    "forgejo____run_mode": "prod",
    "forgejo__session__provider": "db",
    "forgejo__repository__signing__default_trust_model": "collaborator",
    "forgejo__repository__pull_request__default_merge_style": "merge",
}


def test_forgejo_config_valid():
    """ForgejoConfig accepts all valid values without raising."""
    ForgejoConfig(**VALID_KWARGS)


@pytest.mark.parametrize(
    "field, invalid_value",
    [
        ("forgejo__log__level", "VERBOSE"),
        ("forgejo__service__default_user_visibility", "hidden"),
        ("forgejo__service__default_org_visibility", "hidden"),
        ("forgejo____run_mode", "staging"),
        ("forgejo__session__provider", "sqlite"),
        ("forgejo__repository__signing__default_trust_model", "everyone"),
        ("forgejo__repository__pull_request__default_merge_style", "cherry-pick"),
    ],
)
def test_forgejo_config_invalid(field, invalid_value):
    """ForgejoConfig raises ValueError for each invalid field value."""
    kwargs = {**VALID_KWARGS, field: invalid_value}
    with pytest.raises(ValueError):
        ForgejoConfig(**kwargs)
