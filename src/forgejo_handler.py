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
        database_info: dict[str, str] = {},
        log_level: str = "info",
        use_port_in_domain: bool = True,

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
    config["server"] = {
        "SSH_DOMAIN": domain,
        "DOMAIN": domain,
        "HTTP_PORT": str(http_port),
        "ROOT_URL": f"http://{final_domain}/",
        "APP_DATA_PATH": "/data/gitea/data",
        "DISABLE_SSH": "false",
        "SSH_PORT": "22",
        "LFS_START_SERVER": "true",
        "LFS_JWT_SECRET": b64decode(secrets["LFS_JWT_SECRET"]).decode(),
        "OFFLINE_MODE": "true",
    }

    config["lfs"] = {
        "PATH": "/data/gitea/data/lfs"
    }

    config["mailer"] = {
        "ENABLED": "false"
    }

    config["service"] = {
        "REGISTER_EMAIL_CONFIRM": "false",
        "ENABLE_NOTIFY_MAIL": "false",
        "DISABLE_REGISTRATION": "false",
        "ALLOW_ONLY_EXTERNAL_REGISTRATION": "false",
        "ENABLE_CAPTCHA": "true",
        "REQUIRE_SIGNIN_VIEW": "false",
        "DEFAULT_KEEP_EMAIL_PRIVATE": "false",
        "DEFAULT_ALLOW_CREATE_ORGANIZATION": "true",
        "DEFAULT_ENABLE_TIMETRACKING": "true",
        "NO_REPLY_ADDRESS": f"noreply.{domain}"
    }

    config["openid"] = {
        "ENABLE_OPENID_SIGNIN": "true",
        "ENABLE_OPENID_SIGNUP": "true",
        "WHITELISTED_URIS": "login.ubuntu.com"
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

