# Copyright 2025 Nishant Dash
# See LICENSE file for licensing details.

"""Functions for interacting with the workload.

The intention is that this module could be used outside the context of a charm.
"""

from base64 import b64decode
import configparser
import logging

logger = logging.getLogger(__name__)


def generate_config(
        secrets: dict,
        app_name: str = "Forgejo",
        app_slogan: str = "Beyond coding. We Forge.",
        domain: str = "localhost",
        http_port: int = 3000,
        ssh_port: int = 22,
        database_info: dict[str, str] = {},
        log_level: str = "info",
        use_port_in_domain: bool = True,
        tls_enabled: bool = False,
        cert_file: str = "",
        key_file: str = "",
        openid_whitelisted_uris: str = "",
        disable_ssh: bool = False,
        require_signin_view: bool = False,
        default_keep_email_private: bool = True,
        default_allow_create_organization: bool = True,
        enable_openid_signin: bool = True,
        enable_openid_signup: bool = True,
        default_user_visibility: str = "public",
        default_org_visibility: str = "public",
        disable_users_page: bool = False,
        disable_organizations_page: bool = False,
        disable_code_page: bool = False,
        protocol: str = "http",
        disable_plain_registration: bool = True,

    ) -> configparser.ConfigParser:
    """Get the running version of the workload."""

    config = configparser.ConfigParser()
    config.optionxform = str

    config["DEFAULT"] = {
        "APP_NAME": app_name,
        "APP_SLOGAN": app_slogan,
        "RUN_USER": "git",
        "WORK_PATH": "/data/gitea",
        "RUN_MODE": "prod"
    }

    config["database"] = database_info

    config["repository"] = {
        "ROOT": "/data/gitea/data/forgejo-repositories"
    }

    final_domain = f"{domain}:{http_port}" if use_port_in_domain else domain
    protocol = "https" if tls_enabled else "http"
    server_config: dict[str, str] = {
        "SSH_DOMAIN": domain,
        "DOMAIN": domain,
        "HTTP_PORT": str(http_port),
        "ROOT_URL": f"{protocol}://{final_domain}/",
        "APP_DATA_PATH": "/data/gitea/data",
        "DISABLE_SSH": str(disable_ssh).lower(),
        "SSH_PORT": "22", # display port 22 but actually run on port 'ssh_port', because the router and loadbalancer
                          # infront of forgejo will be listening on port 22 
        "SSH_LISTEN_PORT": str(ssh_port),
        "START_SSH_SERVER": "true",
        "LFS_START_SERVER": "true",
        "LFS_JWT_SECRET": b64decode(secrets["LFS_JWT_SECRET"]).decode(),
        "OFFLINE_MODE": "true",
    }
    if tls_enabled and cert_file and key_file:
        server_config["PROTOCOL"] = "https"
        server_config["CERT_FILE"] = cert_file
        server_config["KEY_FILE"] = key_file
    config["server"] = server_config

    config["lfs"] = {
        "PATH": "/data/gitea/data/lfs"
    }

    config["mailer"] = {
        "ENABLED": "false"
    }

    config["service"] = {
        "REGISTER_EMAIL_CONFIRM": "false",
        "REGISTER_MANUAL_CONFIRM": "true",
        "ENABLE_NOTIFY_MAIL": "false",
        "DISABLE_REGISTRATION": str(disable_plain_registration).lower(), # this is only required if you don't want openid login
        "ALLOW_ONLY_EXTERNAL_REGISTRATION": "false",
        "ENABLE_CAPTCHA": "true",
        "REQUIRE_SIGNIN_VIEW": str(require_signin_view).lower(),
        "DEFAULT_KEEP_EMAIL_PRIVATE": str(default_keep_email_private).lower(),
        "DEFAULT_ALLOW_CREATE_ORGANIZATION": str(default_allow_create_organization).lower(),
        "DEFAULT_ENABLE_TIMETRACKING": "true",
        "NO_REPLY_ADDRESS": "noreply.localhost",
        "DEFAULT_USER_VISIBILITY": default_user_visibility,
        "DEFAULT_ORG_VISIBILITY": default_org_visibility,
    }

    openid_config: dict[str, str] = {
        "ENABLE_OPENID_SIGNIN": str(enable_openid_signin).lower(),
        "ENABLE_OPENID_SIGNUP": str(enable_openid_signup).lower(),
    }
    if openid_whitelisted_uris:
        openid_config["WHITELISTED_URIS"] = openid_whitelisted_uris
    config["openid"] = openid_config

    config["explore"] = {
        "DISABLE_USERS_PAGE": str(disable_users_page).lower(),
        "DISABLE_ORGANIZATIONS_PAGE": str(disable_organizations_page).lower(),
        "DISABLE_CODE_PAGE": str(disable_code_page).lower(),
    }

    config["cron.update_checker"] = {
        "ENABLED": "true"
    }

    config["session"] = {
        "PROVIDER": "file"
    }

    config["log"] = {
        "MODE": "console",
        "LEVEL": log_level,
        "ROOT_PATH": "/data/gitea/log"
    }

    config["repository.pull-request"] = {
        "DEFAULT_MERGE_STYLE": "merge"
    }

    config["repository.signing"] = {
        "DEFAULT_TRUST_MODEL": "committer"
    }

    config["security"] = {
        "INSTALL_LOCK": "true",
        "INTERNAL_TOKEN": b64decode(secrets["INTERNAL_TOKEN"]).decode(),
        "PASSWORD_HASH_ALGO": "pbkdf2_hi"
    }

    config["oauth2"] = {
        "ENABLED": "true",
        "JWT_SECRET": b64decode(secrets["JWT_SECRET"]).decode(),
    }

    config["metrics"] = {
        "ENABLED": "true",
        "ENABLED_ISSUE_BY_LABEL": "true",
        "ENABLED_ISSUE_BY_REPOSITORY": "true",
    }

    return config

