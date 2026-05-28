"""Shared constants for the Forgejo K8s charm."""

SERVICE_NAME = "forgejo"
FORGEJO_CLI = "/usr/local/bin/forgejo"
ENVIRONMENT_TO_INI = "/usr/local/bin/environment-to-ini"

CUSTOM_FORGEJO_CONFIG_DIR = "/etc/forgejo/"
CUSTOM_FORGEJO_CONFIG_FILE = CUSTOM_FORGEJO_CONFIG_DIR + "config.ini"

PORT = 3000  # Forgejo's internal listen port (non-privileged, runs as git user uid 1000)
FORGEJO_DATA_DIR = "/data"

FORGEJO_SYSTEM_USER_ID = 1000
FORGEJO_SYSTEM_USER = "git"
FORGEJO_SYSTEM_GROUP_ID = 1000
FORGEJO_SYSTEM_GROUP = "git"
