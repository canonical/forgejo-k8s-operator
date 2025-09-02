# Copyright 2025 Nishant Dash
# See LICENSE file for licensing details.

"""Functions for interacting with the workload.

The intention is that this module could be used outside the context of a charm.
"""

import configparser
import logging
import secrets

logger = logging.getLogger(__name__)

def random_token(length: int = 43) -> str:
    return secrets.token_urlsafe(length)[:length]

def generate_config(
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
        # TODO: Perhaps manage this with juju secrets so its not recreated frequently?
        "LFS_JWT_SECRET": f"`{random_token()}`",
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
        "NO_REPLY_ADDRESS": "noreply.localhost"
    }

    config["openid"] = {
        "ENABLE_OPENID_SIGNIN": "true",
        "ENABLE_OPENID_SIGNUP": "true"
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
        # TODO: Perhaps manage this with juju secrets so its not recreated frequently?
        "INTERNAL_TOKEN": f"`{random_token(length=105)}`",
        "PASSWORD_HASH_ALGO": "pbkdf2_hi"
    }

    config["oauth2"] = {
        # TODO: Perhaps manage this with juju secrets so its not recreated frequently?
        "JWT_SECRET": f"`{random_token()}`",
    }

    config["metrics"] = {
        "ENABLED": "true",
        "ENABLED_ISSUE_BY_LABEL": "true",
        "ENABLED_ISSUE_BY_REPOSITORY": "true",
    }

    return config

