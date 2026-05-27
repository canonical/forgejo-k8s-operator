"""Config utilities for Forgejo charm.

Utilities for mapping charm config to Forgejo environment variables and
validating config values.
"""

import dataclasses

import ops

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


@dataclasses.dataclass(frozen=True, kw_only=True)
class ForgejoConfig:
    """Validated Forgejo configuration (fields correspond to charm config option names)."""

    forgejo__log__level: str
    forgejo__server__domain: str
    forgejo__service__default_user_visibility: str
    forgejo__service__default_org_visibility: str
    forgejo____run_mode: str
    forgejo__session__provider: str
    forgejo__repository__signing__default_trust_model: str
    forgejo__repository__pull_request__default_merge_style: str

    def __post_init__(self):
        """Validate configuration values."""
        _valid_log_levels = {
            "Trace",
            "Debug",
            "Info",
            "Warn",
            "Error",
            "Critical",
            "Fatal",
            "None",
        }
        if self.forgejo__log__level not in _valid_log_levels:
            raise ValueError(f"Invalid log level number, should be one of {_valid_log_levels}")
        _valid_visibility = {"public", "limited", "private"}
        if self.forgejo__service__default_user_visibility not in _valid_visibility:
            raise ValueError(
                "Invalid forgejo__service__default_user_visibility, "
                f"must be one of {_valid_visibility}"
            )
        if self.forgejo__service__default_org_visibility not in _valid_visibility:
            raise ValueError(
                "Invalid forgejo__service__default_org_visibility, "
                f"must be one of {_valid_visibility}"
            )
        if self.forgejo____run_mode not in {"prod", "dev"}:
            raise ValueError("Invalid forgejo____run_mode, must be one of prod, or dev")
        _valid_session_providers = {
            "memory",
            "file",
            "redis",
            "redis-cluster",
            "db",
            "mysql",
            "couchbase",
            "memcache",
            "postgres",
        }
        if self.forgejo__session__provider not in _valid_session_providers:
            raise ValueError(
                f"Invalid forgejo__session__provider, must be one of {_valid_session_providers}"
            )
        _valid_trust_models = {"collaborator", "committer", "collaboratorcommitter"}
        if self.forgejo__repository__signing__default_trust_model not in _valid_trust_models:
            raise ValueError(
                "Invalid forgejo__repository__signing__default_trust_model, "
                f"must be one of {_valid_trust_models}"
            )
        _valid_merge_styles = {"merge", "rebase", "rebase-merge", "squash", "fast-forward-only"}
        if self.forgejo__repository__pull_request__default_merge_style not in _valid_merge_styles:
            raise ValueError(
                "Invalid forgejo__repository__pull_request__default_merge_style, "
                f"must be one of {_valid_merge_styles}"
            )
