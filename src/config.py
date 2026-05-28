"""Config utilities for Forgejo charm.

Utilities for mapping charm config to Forgejo environment variables and
validating config values.
"""

from typing import Literal

import ops
from pydantic import BaseModel, ConfigDict

# Explicit env var name overrides for Juju config options
_CONFIG_KEY_OVERRIDES: dict[str, str] = {
    "forgejo__cron__update_checker__enabled": "FORGEJO__CRON_0X2E_UPDATE_CHECKER__ENABLED",
    "forgejo__repository__signing__default_trust_model": "FORGEJO__REPOSITORY_0X2E_SIGNING__DEFAULT_TRUST_MODEL",  # noqa: E501
    "forgejo__repository__pull_request__default_merge_style": "FORGEJO__REPOSITORY_0X2E_PULL-REQUEST__DEFAULT_MERGE_STYLE",  # noqa: E501
}


def map_config_to_env_vars(
    charm: ops.CharmBase,
    **additional_env,
):
    """Map charm config values to FORGEJO__SECTION__KEY environment variables.

    For each config key the env var name is determined as follows:
    - If the key is present in *key_overrides*, the corresponding value is used
      as the env var name. Use this for Forgejo sections whose names contain
      characters that Juju config option names cannot represent.
    - Otherwise the standard transform applies to keys starting with
      "forgejo__": ``k.upper()``

    The returned dict merges the mapped config with *additional_env*; values in
    *additional_env* take precedence (allowing computed/relational values to
    override defaults).
    """
    env_mapped_config = {}
    for k, v in charm.config.items():
        if str(v).startswith("secret:"):
            # TODO: support secrets
            continue
        if k in _CONFIG_KEY_OVERRIDES:
            env_key = _CONFIG_KEY_OVERRIDES[k]
            env_mapped_config[env_key] = v
        elif k.startswith("forgejo__"):
            env_key = k.upper()
            env_mapped_config[env_key] = v

    return {**env_mapped_config, **additional_env}


class ForgejoConfig(BaseModel):
    """Validated Forgejo configuration."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    forgejo__log__level: Literal[
        "Trace", "Debug", "Info", "Warn", "Error", "Critical", "Fatal", "None"
    ]
    forgejo__server__domain: str
    forgejo__service__default_user_visibility: Literal["public", "limited", "private"]
    forgejo__service__default_org_visibility: Literal["public", "limited", "private"]
    forgejo____run_mode: Literal["prod", "dev"]
    forgejo__session__provider: Literal[
        "memory",
        "file",
        "redis",
        "redis-cluster",
        "db",
        "mysql",
        "couchbase",
        "memcache",
        "postgres",
    ]
    forgejo__repository__signing__default_trust_model: Literal[
        "collaborator", "committer", "collaboratorcommitter"
    ]
    forgejo__repository__pull_request__default_merge_style: Literal[
        "merge", "rebase", "rebase-merge", "squash", "fast-forward-only"
    ]
