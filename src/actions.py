"""Juju action handlers for the Forgejo K8s charm."""

import shlex
from typing import cast

import ops

FORGEJO_CLI = "/usr/local/bin/forgejo"
FORGEJO_CONFIG_FILE = "/etc/forgejo/config.ini"


def _exec_as_git(container: ops.Container, cmd: str) -> str:
    """Execute a Forgejo CLI command as the git user and return stdout."""
    argv = ["su", "git", "-c", cmd]
    output, _ = container.exec(argv).wait_output()
    return output


def on_generate_runner_secret(event: ops.ActionEvent, container: ops.Container) -> None:
    """Generate a new runner secret and return it as action output."""
    params = event.params
    name = params.get("name", "runner")
    labels = params.get("labels", "docker")
    scope = params.get("scope", None)

    # Generate the secret
    cmd_parts: list[str] = cast(
        list[str], f"{FORGEJO_CLI} forgejo-cli actions generate-secret".split()
    )
    secret, _ = container.exec(cmd_parts).wait_output()

    # Register the runner with the generated secret
    register_cmd = (
        f"{shlex.quote(FORGEJO_CLI)} --config={FORGEJO_CONFIG_FILE}"
        f" forgejo-cli actions register"
        f" --secret {shlex.quote(secret)}"
        f" --labels {shlex.quote(labels)}"
        f" --name {shlex.quote(name)}"
    )
    if scope:
        register_cmd += f" --scope {shlex.quote(scope)}"

    _exec_as_git(container, register_cmd)
    event.set_results({"runner-secret": secret})


def on_create_admin_user(event: ops.ActionEvent, container: ops.Container) -> None:
    """Create an admin user in Forgejo."""
    username = event.params.get("username")
    email = event.params.get("email")
    if not username or not email:
        event.fail("username, password, and email parameters are required")
        return

    cmd = (
        f"{shlex.quote(FORGEJO_CLI)} --config={FORGEJO_CONFIG_FILE} admin user create"
        f" --username {shlex.quote(username)}"
        f" --email {shlex.quote(email)}"
        f" --admin"
        f" --random-password"
    )
    output = _exec_as_git(container, cmd)
    event.set_results({"output": output})


def on_generate_user_token(event: ops.ActionEvent, container: ops.Container) -> None:
    """Generate an API access token for the specified Forgejo user."""
    username = event.params.get("username")
    token_name = event.params.get("token-name", "charm-token")
    scopes = event.params.get("scopes", "all")
    if not username:
        event.fail("username parameter is required")
        return

    cmd = (
        f"{shlex.quote(FORGEJO_CLI)} --config={FORGEJO_CONFIG_FILE}"
        f" admin user generate-access-token"
        f" --username {shlex.quote(username)}"
        f" --token-name {shlex.quote(token_name)}"
        f" --scopes {shlex.quote(scopes)}"
        f" --raw"
    )
    try:
        output = _exec_as_git(container, cmd)
    except ops.pebble.ExecError as e:
        event.fail(f"Failed to generate token: {e.stderr}")
        return
    event.set_results({"token": output.strip()})


def on_reset_user_password(event: ops.ActionEvent, container: ops.Container) -> None:
    """Reset a Forgejo user's password to a new random value."""
    username = event.params.get("username")
    password = event.params.get("password")
    if not username:
        event.fail("username parameter is required")
        return
    if not password:
        event.fail("password parameter is required")
        return

    cmd = (
        f"{shlex.quote(FORGEJO_CLI)} --config={FORGEJO_CONFIG_FILE}"
        f" admin user change-password"
        f" --username {shlex.quote(username)}"
        f" --password {shlex.quote(password)}"
    )
    try:
        output = _exec_as_git(container, cmd)
    except ops.pebble.ExecError as e:
        event.fail(f"Failed to reset password: {e.stderr}")
        return
    event.set_results({"output": output.strip()})
