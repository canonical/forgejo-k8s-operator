#!/usr/bin/env python3
# Copyright 2025 Nishant Dash
# See LICENSE file for licensing details.

"""Forgejo K8s Charm."""

from base64 import b64encode
import dataclasses
from io import StringIO
import logging
import ops
import re
from typing import Optional
import secrets
import shlex

from charms.data_platform_libs.v0.data_interfaces import DatabaseRequires
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.traefik_k8s.v0.traefik_route import TraefikRouteRequirer
from lightkube import Client
from lightkube.resources.core_v1 import Secret
from lightkube.models.meta_v1 import ObjectMeta

from certificates import CertHandler
from forgejo_handler import generate_config

logger = logging.getLogger(__name__)

SERVICE_NAME = "forgejo"  # Name of Pebble service that runs in the workload container.
FORGEJO_CLI = "/usr/local/bin/forgejo"
CUSTOM_FORGEJO_CONFIG_DIR = "/etc/forgejo/"
CUSTOM_FORGEJO_CONFIG_FILE = CUSTOM_FORGEJO_CONFIG_DIR + "config.ini"
PORT = 3000
SSH_PORT = 22222
FORGEJO_DATA_DIR = "/data"
FORGEJO_SYSTEM_USER_ID = 1000
FORGEJO_SYSTEM_USER = "git"
FORGEJO_SYSTEM_GROUP_ID = 1000
FORGEJO_SYSTEM_GROUP = "git"


@dataclasses.dataclass(frozen=True, kw_only=True)
class ForgejoConfig:
    """Configuration for the Forgejo k8s charm."""

    log_level: str = "info"
    domain: str = "forgejo.internal"
    openid_whitelisted_uris: str = ""
    disable_ssh: bool = False
    require_signin_view: bool = False
    default_keep_email_private: bool = True
    default_allow_create_organization: bool = True
    enable_openid_signin: bool = True
    enable_openid_signup: bool = True
    default_user_visibility: str = "public"
    default_org_visibility: str = "public"
    disable_users_page: bool = False
    disable_organizations_page: bool = False
    disable_code_page: bool = False
    disable_plain_registration: bool = True

    def __post_init__(self):
        """Configuration validation."""
        if self.log_level not in ['trace', 'debug', 'info', 'warn', 'error', 'fatal']:
            raise ValueError('Invalid log level, should be one of trace, debug, info, warn, error, or fatal')
        _valid_visibility = {'public', 'limited', 'private'}
        if self.default_user_visibility not in _valid_visibility:
            raise ValueError('Invalid default-user-visibility, must be one of public, limited, or private')
        if self.default_org_visibility not in _valid_visibility:
            raise ValueError('Invalid default-org-visibility, must be one of public, limited, or private')
            


def random_token(length: int = 43) -> str:
    return secrets.token_urlsafe(length)[:length]


class ForgejoK8SOperatorCharm(ops.CharmBase):
    """Forgejo K8s Charm."""

    def __init__(self, framework: ops.Framework) -> None:
        super().__init__(framework)
        self.reconcile_ports()

        self.ingress = TraefikRouteRequirer(
            self, self.model.get_relation("ingress"), "ingress", raw=True
        )

        # observability endpoint support
        self._prometheus_scraping = MetricsEndpointProvider(
            self,
            relation_name='metrics-endpoint',
            jobs=[{'static_configs': [{'targets': [f'*:{PORT}']}]}],
            refresh_event=self.on.config_changed,
        )
        self._logging = LogForwarder(self, relation_name='logging')
        self._grafana_dashboards = GrafanaDashboardProvider(
            self, relation_name='grafana-dashboard'
        )

        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.forgejo_pebble_ready, self.reconcile)
        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.ingress.on.ready, self.reconcile)
        framework.observe(self.on.collect_unit_status, self._on_collect_status)
        framework.observe(getattr(self.on, "data_storage_attached"), self._on_storage_attached)

        # actions
        framework.observe(self.on.generate_runner_secret_action, self._on_generate_runner_secret)
        framework.observe(self.on.create_admin_user_action, self._on_create_admin_user)
        framework.observe(self.on.generate_user_token_action, self._on_generate_user_token)
        framework.observe(self.on.reset_user_password_action, self._on_reset_user_password)

        # TLS certificates support
        self.cert_handler = CertHandler(
            self,
            common_name=self.model.config.get("domain") or self.app.name,
            events=[self.on.config_changed, self.on.forgejo_pebble_ready],
        )
        framework.observe(
            self.cert_handler.certificates.on.certificate_available, self._on_certificates_available
        )
        framework.observe(self.on["certificates"].relation_changed, self._on_certificates_available)
        framework.observe(self.on["certificates"].relation_departed, self._on_certificates_removed)
        framework.observe(self.on["certificates"].relation_broken, self._on_certificates_removed)

        # database support
        self.database = DatabaseRequires(
            self,
            relation_name='database',
            database_name=self.database_name,
        )
        framework.observe(self.database.on.database_created, self.reconcile)
        framework.observe(self.database.on.endpoints_changed, self.reconcile)

        self._name = "forgejo"
        self.container = self.unit.get_container(self._name)
        self.pebble_service_name = SERVICE_NAME


    def _on_install(self, _: ops.InstallEvent):
        self.get_or_create_forgejo_secrets()


    @property
    def database_name(self):
        return f"{self.model.name}-{self.app.name}"


    @property
    def k8s_secrets_name(self):
        return f"{self.app.name}-secrets"


    def get_or_create_forgejo_secrets(self) -> dict | None:
        """Create one-time Forgejo secrets in Kubernetes."""
        client = Client()

        try:
            secret = client.get(Secret, name=self.k8s_secrets_name, namespace=self.model.name)
            data = secret.data
            logger.info(f"Secret '{self.k8s_secrets_name}' found. Using existing values.")
        except Exception as e:
            if "not found" not in str(e).lower():
                logger.error(f"Something went wrong when trying to get k8s secret {self.k8s_secrets_name}, got {e}")
                return

            logger.info(f"K8s secret {self.k8s_secrets_name} not found, creating it...")

            data = {
                "LFS_JWT_SECRET": b64encode(random_token().encode()).decode(),
                "INTERNAL_TOKEN": b64encode(random_token(105).encode()).decode(),
                "JWT_SECRET": b64encode(random_token().encode()).decode(),
            }
            secret = Secret(
                metadata=ObjectMeta(name=self.k8s_secrets_name, namespace=self.model.name),
                type="Opaque",
                data=data,
            )
            client.create(secret)
            logger.info(f"Secret '{self.k8s_secrets_name}' created successfully.")

        return data


    def _on_collect_status(self, event: ops.CollectStatusEvent) -> None:
        config = None
        try:
            config = self.load_config(ForgejoConfig)
        except ValueError as e:
            event.add_status(ops.BlockedStatus(str(e)))
        if config:
            if not config.domain:
                event.add_status(ops.BlockedStatus('domain config needs to be set'))
        if not self.model.get_relation('database'):
            # We need the user to do 'juju integrate'.
            event.add_status(ops.BlockedStatus('Waiting for database relation'))
        elif not self.database.fetch_relation_data():
            # We need the Forgejo <-> Postgresql relation to finish integrating.
            event.add_status(ops.WaitingStatus('Waiting for database relation'))
        if not self.ingress.is_ready():
            # We need the Forgejo <-> Ingress relation to finish integrating.
            event.add_status(ops.WaitingStatus('Waiting for ingress relation'))
        if self.model.get_relation('certificates') and not self.cert_handler.configure_certs():
            event.add_status(ops.WaitingStatus('Waiting for TLS certificate'))
        try:
            status = self.container.get_service(self.pebble_service_name)
        except (ops.pebble.APIError, ops.pebble.ConnectionError, ops.ModelError):
            event.add_status(ops.MaintenanceStatus('Waiting for Pebble in workload container'))
        else:
            if not status.is_running():
                event.add_status(ops.MaintenanceStatus('Waiting for Forgejo to start up'))
        # If nothing is wrong, then the status is active.
        if config:
            scheme = "https" if self._tls_enabled else "http"
            event.add_status(ops.ActiveStatus(f"Serving at {scheme}://{config.domain}"))
        else:
            event.add_status(ops.ActiveStatus())


    @property
    def _forgejo_version(self) -> Optional[str]:
        """Returns the version of Forgejo.

        Returns:
            A string equal to the Forgejo version.
        """
        if not self.container.can_connect():
            return None
        version_output, _ = self.container.exec([FORGEJO_CLI, "--version"]).wait_output()
        # Output looks like this:
        # Forgejo version 11.0.3+gitea-1.22.0 (release name 11.0.3) built with ...
        result = re.search(r"version (\d*\.\d*\.\d*)", version_output)
        if result is None:
            return result
        return result.group(1)


    def _get_pebble_layer(self) -> ops.pebble.Layer:
        """A Pebble layer for the Forgejo service."""
        pebble_layer: ops.pebble.LayerDict = {
            'summary': 'Forgejo service',
            'description': 'pebble config layer for the Forgejo server',
            'services': {
                self.pebble_service_name: {
                    'override': 'replace',
                    'summary': 'Forgejo service',
                    'command': f"{FORGEJO_CLI} web --config={CUSTOM_FORGEJO_CONFIG_FILE}",
                    'startup': 'enabled',
                    'user-id': FORGEJO_SYSTEM_USER_ID,
                    'group-id': FORGEJO_SYSTEM_GROUP_ID,
                    "working-dir": FORGEJO_DATA_DIR,
                }
            },
        }
        return ops.pebble.Layer(pebble_layer)


    def _on_config_changed(self, e: ops.ConfigChangedEvent):
        self.reconcile(e)
        if not self.container.get_plan().services.get(self.pebble_service_name):
            logger.error("Cannot (re)start service: service does not (yet) exist.")
            return
        logger.info(f"Restarting service {self.pebble_service_name}")
        # TODO: consider and test forgejo manager restart for a more graceful approach
        self.container.restart(self.pebble_service_name)


    def reconcile(self, _: ops.HookEvent) -> None:
        self.unit.status = ops.MaintenanceStatus("starting workload")
        try:
            config = self.load_config(ForgejoConfig)
        except ValueError as e:
            logger.error('Configuration error: %s', e)
            self.unit.status = ops.BlockedStatus(str(e))
            return

        try:
            db_data = self.fetch_postgres_relation_data()

            # Configure TLS if certificates relation is available.
            # Forgejo always listens on PORT (3000) regardless of TLS state;
            # the protocol is controlled via the PROTOCOL/CERT_FILE/KEY_FILE config.
            tls_ready = self.cert_handler.configure_certs()

            if self.ingress.is_ready() and self.unit.is_leader():
                if config.domain:
                    logger.info(
                        f"Config domain {config.domain} is valid, submitting traefik route"
                    )
                    self.ingress.submit_to_traefik(
                        self.get_dynamic_traefik_route_configuration(config.domain),
                        static=self.get_static_traefik_route_configuration,
                    )
                else:
                    logger.error(f"No domain set in charm, so can not generate ingress route")

            secrets = self.get_or_create_forgejo_secrets()
            if not secrets:
                self.unit.status = ops.BlockedStatus(f"Can not get forgejo secrets {self.k8s_secrets_name} to start")
                return

            scheme = self.fetch_ingress_relation_data()
            protocol = "http"
            if scheme:
                scheme = scheme.lower()
                if scheme not in ["http", "https"]:
                    logger.warning(f"Got scheme {scheme} from traefik databag, but only http or https is supported, so falling back to http")
                    protocol = "http"
                else:
                    protocol = scheme

            # write the config file to the forgejo container's filesystem
            cfg = generate_config(
                secrets=secrets,
                domain=config.domain,
                log_level=config.log_level,
                database_info=db_data,
                http_port=PORT,
                ssh_port=SSH_PORT,
                use_port_in_domain=False,
                tls_enabled=tls_ready,
                cert_file=self.cert_handler.cert_path if tls_ready else "",
                key_file=self.cert_handler.key_path if tls_ready else "",
                openid_whitelisted_uris=config.openid_whitelisted_uris,
                disable_ssh=config.disable_ssh,
                require_signin_view=config.require_signin_view,
                default_keep_email_private=config.default_keep_email_private,
                default_allow_create_organization=config.default_allow_create_organization,
                enable_openid_signin=config.enable_openid_signin,
                enable_openid_signup=config.enable_openid_signup,
                default_user_visibility=config.default_user_visibility,
                default_org_visibility=config.default_org_visibility,
                disable_users_page=config.disable_users_page,
                disable_organizations_page=config.disable_organizations_page,
                disable_code_page=config.disable_code_page,
                protocol=protocol,
                disable_plain_registration=config.disable_plain_registration,
            )
            buf = StringIO()
            cfg.write(buf)
            self.container.push(
                CUSTOM_FORGEJO_CONFIG_FILE,
                buf.getvalue(),
                make_dirs=True,
                user_id=FORGEJO_SYSTEM_USER_ID,
                user=FORGEJO_SYSTEM_USER,
                group_id=FORGEJO_SYSTEM_GROUP_ID
            )

            self.container.add_layer('forgejo', self._get_pebble_layer(), combine=True)
            logger.info("Added updated layer 'forgejo' to Pebble plan")

            # Tell Pebble to incorporate the changes, including restarting the
            # service if required.
            self.container.replan()
            logger.info(f"Replanned with '{self.pebble_service_name}' service")

            if version := self._forgejo_version:
                self.unit.set_workload_version(version)
            else:
                logger.debug("Cannot set workload version at this time: could not get Forgejo version.")
        # @TODO: Extend exception handling
        except (ops.pebble.APIError, ops.pebble.ConnectionError) as e:
            logger.info('Unable to connect to Pebble: %s', e)


    @property
    def _tls_enabled(self) -> bool:
        """Return True if TLS certificates are provisioned in the relation data."""
        if not self.model.get_relation('certificates'):
            return False
        return self.cert_handler._certificate_is_available()

    def reconcile_ports(self):
        """Open necessary (and close no longer needed) workload ports."""
        planned_ports = {
            ops.model.OpenedPort("tcp", PORT),
            ops.model.OpenedPort("tcp", SSH_PORT),
        }

        actual_ports = self.unit.opened_ports()

        # Ports may change across an upgrade, so need to sync
        ports_to_close = actual_ports.difference(planned_ports)
        for p in ports_to_close:
            self.unit.close_port(p.protocol, p.port)

        new_ports_to_open = planned_ports.difference(actual_ports)
        for p in new_ports_to_open:
            self.unit.open_port(p.protocol, p.port)


    def fetch_postgres_relation_data(self) -> dict[str, str]:
        """Fetch postgres relation data.

        This function retrieves relation data from a postgres database using
        the `fetch_relation_data` method of the `database` object. The retrieved data is
        then logged for debugging purposes, and any non-empty data is processed to extract
        endpoint information, username, and password. This processed data is then returned as
        a dictionary. If no data is retrieved, the unit is set to waiting status and
        the program exits with a zero status code.
        """
        relations = self.database.fetch_relation_data()
        logger.debug('Got following database data: %s', relations)
        for data in relations.values():
            if not data:
                continue
            logger.info('New database endpoint is %s', data['endpoints'])
            host, port = data['endpoints'].split(':')
            db_data = {
                "DB_TYPE": "postgres",
                'HOST': host,
                'PORT': port,
                'NAME': self.database_name,
                'USER': data['username'],
                'PASSWD': data['password'],
                "SCHEMA": "",
                "SSL_MODE": "disable",
                "LOG_SQL": "false",
            }
            return db_data
        return {}


    def fetch_ingress_relation_data(self) -> str | None:
        """Fetch ingress relation data.

        We need to get the scheme from traefik, to know if we are on http or https.
        """
        logger.info(f"Ingress object has scheme {self.ingress.scheme}")
        traefik_route_relation = self.model.get_relation("ingress")
        if traefik_route_relation:
            return traefik_route_relation.data[traefik_route_relation.app].get("scheme")


    def traefik_service_name(self, service_type: str = "http") -> str:
        return f"{self.model.name}-{self.model.app.name}-{service_type}-service"


    def get_dynamic_traefik_route_configuration(self, domain: str) -> dict:
        """Configure a route from traefik to forgejo."""
        return {
            "http": {
                "routers": {
                    f"{self.model.name}-{self.app.name}-router": {
                        "rule": f"Host(`{domain}`)", # "ClientIP(`0.0.0.0/0`)"
                        "service": self.traefik_service_name(),
                        "entryPoints": ["web"],
                    }
                },
                "services": {
                    self.traefik_service_name(): {
                        "loadBalancer": {
                            "servers": [
                                {"url": f"http://{k8s_service}:{PORT}"}
                            ]
                        }
                    }
                }
            },
            "tcp": {
                "routers": {
                    f"{self.model.name}-{self.app.name}-ssh-router": {
                        "rule": "HostSNI(`*`)",
                        "service": self.traefik_service_name("ssh"),
                        "entryPoints": ["ssh"],
                    }
                },
                "services": {
                    self.traefik_service_name("ssh"): {
                        "loadBalancer": {
                            "servers": [
                                {"address": f"{self.app.name}.{self.model.name}.svc.cluster.local:{SSH_PORT}"}
                            ]
                        }
                    }
                }
            }
        }


    @property
    def get_static_traefik_route_configuration(self) -> dict:
        """Only generate static configs for ssh port as port 80 and 443 are generated already.

        Forgejo's config SSH_PORT will display port 22 in the clone url, but it will actually be listening on
        SSH_LISTEN_PORT. This routing will be:
          user -> loadBalancer (tcp/22) -> ingress (tcp/22) -> forgejo (tcp/SSH_LISTEN_PORT)
        """
        return {
            "entryPoints": {
                "ssh": {
                    "address": ":22"
                }
            }
        }


    def _on_certificates_available(self, event: ops.EventBase) -> None:
        """Handle new/updated TLS certificate - switch Forgejo to HTTPS."""
        logger.info("TLS certificate available, switching to HTTPS")
        self.reconcile(event)
        self._restart_service()

    def _on_certificates_removed(self, event: ops.EventBase) -> None:
        """Handle TLS certificate removal - switch Forgejo back to HTTP."""
        logger.info("TLS certificates removed, switching back to HTTP")
        if self.container.can_connect():
            self.cert_handler.remove_certs()
        self.reconcile(event)
        self._restart_service()

    def _restart_service(self) -> None:
        """Restart the Forgejo service if it is currently running."""
        if not self.container.can_connect():
            return
        try:
            if self.container.get_service(self.pebble_service_name).is_running():
                self.container.restart(self.pebble_service_name)
                logger.info(f"Restarted {self.pebble_service_name} to pick up certificate changes")
        except (ops.pebble.APIError, ops.ModelError) as e:
            logger.warning("Could not restart service: %s", e)

    def _on_storage_attached(self, _: ops.StorageAttachedEvent) -> None:
        self.container.exec(["chown", f"{FORGEJO_SYSTEM_USER}:{FORGEJO_SYSTEM_GROUP}", FORGEJO_DATA_DIR])


    def _on_generate_runner_secret(self, event: ops.ActionEvent) -> None:
        """Generate a new runner secret and return it as action output."""
        # SECRET=$(forgejo forgejo-cli actions generate-secret)
        # forgejo forgejo-cli actions register --secret $SECRET --labels "docker" --labels "machine2"
        params = event.params
        name = params.get("name", "runner")
        labels = params.get("labels", "docker")
        scope = params.get("scope", None)
        add_scope = ""
        if scope:
            add_scope = f"--scope {scope}"
        # generate the secret
        cmd = f"{FORGEJO_CLI} forgejo-cli actions generate-secret"
        secret, _ = self.container.exec(cmd.split()).wait_output()
        # register the runner with the generated secret
        register_cmd = (
          f"{shlex.quote(FORGEJO_CLI)} --config=/etc/forgejo/config.ini forgejo-cli actions register "
          f"--secret {shlex.quote(secret)} "
          f"--labels {shlex.quote(labels)} "
          f"--name {shlex.quote(name)} "
          f"{add_scope}".strip()
        )
        argv = ["su", "git", "-c", register_cmd]
        self.container.exec(argv).wait_output()
        # send the secret back as action output
        event.set_results({"runner-secret": secret})


    def _on_create_admin_user(self, event: ops.ActionEvent) -> None:
        """Create an admin user in Forgejo."""
        params = event.params
        username = params.get("username")
        email = params.get("email")
        if not username or not email:
            event.fail("username, password, and email parameters are required")
            return
        cmd = (
          f"{shlex.quote(FORGEJO_CLI)} --config=/etc/forgejo/config.ini admin user create "
          f"--username {shlex.quote(username)} "
          f"--email {shlex.quote(email)} "
          f"--admin "
          f"--random-password"
        )
        argv = ["su", "git", "-c", cmd]
        output, _ = self.container.exec(argv).wait_output()
        event.set_results({"output": output})


    def _on_generate_user_token(self, event: ops.ActionEvent) -> None:
        """Generate an API access token for the specified Forgejo user."""
        params = event.params
        username = params.get("username")
        token_name = params.get("token-name", "charm-token")
        scopes = params.get("scopes", "all")
        if not username:
            event.fail("username parameter is required")
            return
        cmd = (
          f"{shlex.quote(FORGEJO_CLI)} --config=/etc/forgejo/config.ini admin user generate-access-token "
          f"--username {shlex.quote(username)} "
          f"--token-name {shlex.quote(token_name)} "
          f"--scopes {shlex.quote(scopes)} "
          f"--raw"
        )
        argv = ["su", "git", "-c", cmd]
        try:
            output, _ = self.container.exec(argv).wait_output()
        except ops.pebble.ExecError as e:
            event.fail(f"Failed to generate token: {e.stderr}")
            return
        event.set_results({"token": output.strip()})


    def _on_reset_user_password(self, event: ops.ActionEvent) -> None:
        """Reset a Forgejo user's password to a new random value."""
        username = event.params.get("username")
        password = event.params.get("password")
        if not username:
            event.fail("username parameter is required")
            return
        cmd = (
          f"{shlex.quote(FORGEJO_CLI)} --config=/etc/forgejo/config.ini admin user change-password "
          f"--username {shlex.quote(username)} "
          f"--password {shlex.quote(password)}"
        )
        argv = ["su", "git", "-c", cmd]
        try:
            output, _ = self.container.exec(argv).wait_output()
        except ops.pebble.ExecError as e:
            event.fail(f"Failed to reset password: {e.stderr}")
            return
        event.set_results({"output": output.strip()})


if __name__ == "__main__":  # pragma: nocover
    ops.main(ForgejoK8SOperatorCharm)
