"""Config utilities for Forgejo charm.

Utilities for mapping charm config to Forgejo environment variables and
validating config values.
"""

import logging
from typing import Literal

import ops
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

logger = logging.getLogger(__name__)

# Explicit mapping of Juju config option names (kebab-case, as declared in
# charmcraft.yaml) to the FORGEJO__SECTION__KEY environment variable that
# environment-to-ini expects. Every option must have an entry here.
_CONFIG_KEY_TO_ENV_VAR: dict[str, str] = {
    "log-level": "FORGEJO__LOG__LEVEL",
    "server-domain": "FORGEJO__SERVER__DOMAIN",
    "openid-whitelisted-uris": "FORGEJO__OPENID__WHITELISTED_URIS",
    "server-disable-ssh": "FORGEJO__SERVER__DISABLE_SSH",
    "service-disable-registration": "FORGEJO__SERVICE__DISABLE_REGISTRATION",
    "service-require-signin-view": "FORGEJO__SERVICE__REQUIRE_SIGNIN_VIEW",
    "service-default-allow-create-organization": "FORGEJO__SERVICE__DEFAULT_ALLOW_CREATE_ORGANIZATION",  # noqa: E501
    "admin-disable-regular-org-creation": "FORGEJO__ADMIN__DISABLE_REGULAR_ORG_CREATION",
    "admin-user-disabled-features": "FORGEJO__ADMIN__USER_DISABLED_FEATURES",
    "admin-external-user-disabled-features": "FORGEJO__ADMIN__EXTERNAL_USER_DISABLE_FEATURES",
    "service-default-user-visibility": "FORGEJO__SERVICE__DEFAULT_USER_VISIBILITY",
    "service-default-org-visibility": "FORGEJO__SERVICE__DEFAULT_ORG_VISIBILITY",
    "explore-disable-users-page": "FORGEJO__SERVICE_0X2E_EXPLORE__DISABLE_USERS_PAGE",
    "explore-disable-organizations-page": "FORGEJO__SERVICE_0X2E_EXPLORE__DISABLE_ORGANIZATIONS_PAGE",  # noqa: E501
    "explore-disable-code-page": "FORGEJO__SERVICE_0X2E_EXPLORE__DISABLE_CODE_PAGE",
    "app-name": "FORGEJO____APP_NAME",
    "app-slogan": "FORGEJO____APP_SLOGAN",
    "server-ssh-port": "FORGEJO__SERVER__SSH_PORT",
    "server-root-url": "FORGEJO__SERVER__ROOT_URL",
    "mailer-enabled": "FORGEJO__MAILER__ENABLED",
    "service-register-email-confirm": "FORGEJO__SERVICE__REGISTER_EMAIL_CONFIRM",
    "service-register-manual-confirm": "FORGEJO__SERVICE__REGISTER_MANUAL_CONFIRM",
    "service-enable-notify-mail": "FORGEJO__SERVICE__ENABLE_NOTIFY_MAIL",
    "service-allow-only-external-registration": "FORGEJO__SERVICE__ALLOW_ONLY_EXTERNAL_REGISTRATION",  # noqa: E501
    "session-provider": "FORGEJO__SESSION__PROVIDER",
    "oauth2-enabled": "FORGEJO__OAUTH2__ENABLED",
    "metrics-enabled": "FORGEJO__METRICS__ENABLED",
    "migrations-allowed-domains": "FORGEJO__MIGRATIONS__ALLOWED_DOMAINS",
    "packages-enabled": "FORGEJO__PACKAGES__ENABLED",
    "security-secret-key": "FORGEJO__SECURITY__SECRET_KEY",
    "security-internal-token": "FORGEJO__SECURITY__INTERNAL_TOKEN",
    "server-lfs-jwt-secret": "FORGEJO__SERVER__LFS_JWT_SECRET",
    "metrics-token": "FORGEJO__METRICS__TOKEN",
    "proxy-enabled": "FORGEJO__PROXY__PROXY_ENABLED",
}


def _fetch_secret(charm: ops.CharmBase, secret_id: str) -> str | None:
    """Fetch a Juju secret value."""
    try:
        secret = charm.model.get_secret(id=secret_id)
        content = secret.get_content(refresh=True)
    except (ops.SecretNotFoundError, ops.model.ModelError) as e:
        logger.error("Cannot access Juju secret %s: %s", secret_id, e)
        return None

    value = content.get("value")
    if value is None:
        logger.warning(
            "Juju secret %s has no 'value' key; use juju add-secret ... value=<secret>",
            secret_id,
        )
    return value


def map_config_to_env_vars(
    charm: ops.CharmBase,
    **additional_env,
):
    """Map charm config values to FORGEJO__SECTION__KEY environment variables.

    Each Juju config key is looked up in *_CONFIG_KEY_TO_ENV_VAR* to determine
    its corresponding environment variable name; keys with no entry there
    (e.g. unrelated charm-internal state) are ignored.

    The returned dict merges the mapped config with *additional_env*; values in
    *additional_env* take precedence (allowing computed/relational values to
    override defaults).
    """
    env_mapped_config = {}
    for k, v in charm.config.items():
        env_key = _CONFIG_KEY_TO_ENV_VAR.get(k)
        if env_key is None:
            continue
        if str(v).startswith("secret:"):
            secret = _fetch_secret(charm, str(v))
            if secret is None:
                continue
            v = secret
        env_mapped_config[env_key] = v

    return {**env_mapped_config, **additional_env}


class ForgejoConfig(BaseModel):
    """Validated Forgejo configuration."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    log_level: Literal["Trace", "Debug", "Info", "Warn", "Error", "Critical", "Fatal", "None"]
    server_domain: str
    service_default_user_visibility: Literal["public", "limited", "private"]
    service_default_org_visibility: Literal["public", "limited", "private"]
    session_provider: Literal[
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


class ForgejoStorageConfig(BaseModel):
    """Forgejo S3/MinIO storage settings.

    Dump with `model_dump(by_alias=True)` to get the `FORGEJO__STORAGE__*`
    mapping Forgejo expects.
    """

    storage_type: str = Field("minio", serialization_alias="FORGEJO__STORAGE__STORAGE_TYPE")
    endpoint: str = Field("", serialization_alias="FORGEJO__STORAGE__MINIO_ENDPOINT")
    access_key_id: str = Field("", serialization_alias="FORGEJO__STORAGE__MINIO_ACCESS_KEY_ID")
    secret_access_key: str = Field(
        "", serialization_alias="FORGEJO__STORAGE__MINIO_SECRET_ACCESS_KEY"
    )
    bucket: str = Field("forgejo", serialization_alias="FORGEJO__STORAGE__MINIO_BUCKET")
    location: str = Field("", serialization_alias="FORGEJO__STORAGE__MINIO_LOCATION")
    base_path: str = Field("", serialization_alias="FORGEJO__STORAGE__MINIO_BASE_PATH")
    use_ssl: bool = Field(True, serialization_alias="FORGEJO__STORAGE__MINIO_USE_SSL")

    @field_validator("endpoint", mode="before")
    @classmethod
    def _strip_scheme(cls, v: str) -> str:
        return v.removeprefix("https://").removeprefix("http://")

    @field_serializer("use_ssl")
    def _ssl_to_str(self, value: bool) -> str:
        return "true" if value else "false"

    @classmethod
    def from_s3_info(cls, s3_info: dict[str, str]) -> "ForgejoStorageConfig":
        """Build from the s3-credentials relation payload (note the dashed keys)."""
        return cls(
            storage_type=s3_info.get("storage-type", "minio"),
            endpoint=s3_info.get("endpoint", ""),
            access_key_id=s3_info.get("access-key", ""),
            secret_access_key=s3_info.get("secret-key", ""),
            bucket=s3_info.get("bucket", "forgejo"),
            location=s3_info.get("region", ""),
            base_path=s3_info.get("path", ""),
            use_ssl=s3_info.get("use-ssl", "true").lower() == "true",
        )


class TraefikSSHConfig(BaseModel):
    """SSH-related Traefik ingress settings read from charm config."""

    model_config = ConfigDict(frozen=True)

    ssh_enabled: bool
    ssh_port: int
    ssh_listen_port: int

    @classmethod
    def from_charm_config(cls, config: ops.ConfigData) -> "TraefikSSHConfig":
        """Build from the charm's live config."""
        return cls(
            ssh_enabled=not bool(config.get("server-disable-ssh", False)),
            ssh_port=int(config.get("server-ssh-port", 2222)),
            # Not user-configurable: the built-in SSH server always listens on
            # 2222 inside the container.
            ssh_listen_port=2222,
        )
