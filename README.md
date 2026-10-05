# forgejo-k8s-operator

Charmed k8s operator for forgejo.


## Expected to be used with

* Postgresql (or pgbouncer) for the database backend
* Traefik for ingress

Example deployment:

```sh
juju deploy forgejo-k8s
juju deploy postgresql-k8s --channel=14/stable --trust
juju deploy traefik-k8s --config external_hostname=internal --trust

juju integrate forgejo-k8s postgresql-k8s
juju integrate forgejo-k8s traefik-k8s
```

```console
Unit               Workload  Agent  Address      Ports  Message
forgejo-k8s/0*     active    idle   10.1.131.36         Serving at forgejo.internal
postgresql-k8s/0*  active    idle   10.1.131.7          Primary
traefik-k8s/0*     active    idle   10.1.131.37         Serving at internal
````

```console
# curl -I -H "Host: forgejo.internal" http://<EXTERNAL-TRAEFIK-LOADBALANCER-SERVICE-IP>/
HTTP/1.1 200 OK
Date: Tue, 02 Sep 2025 19:40:37 GMT
```

## Integrations

| Endpoint | Direction | Interface | Purpose |
| --- | --- | --- | --- |
| `database` | requires | `postgresql_client` | Required. Database backend for Forgejo (e.g. `postgresql-k8s`, optionally behind `pgbouncer-k8s`). |
| `ingress` | requires | `traefik_route` | Ingress to Forgejo's web UI/API and, if enabled, Git-over-SSH, via `traefik-k8s`. |
| `certificates` | requires | `tls-certificates` | Optional. TLS certificate for HTTPS (e.g. `self-signed-certificates`). Removing the relation reverts Forgejo to HTTP. |
| `s3-credentials` | requires | `s3` | Optional. S3-compatible object storage for attachments, LFS, avatars, repo-archives, packages, and actions artifacts (e.g. `s3-integrator`). |
| `logging` | requires | `loki_push_api` | Optional. Forwards Forgejo logs to Loki (e.g. via `grafana-agent-k8s` or `loki-k8s`). |
| `metrics-endpoint` | provides | `prometheus_scrape` | Optional. Exposes Forgejo's `/metrics` endpoint for scraping by `prometheus-k8s`. |
| `grafana-dashboard` | provides | `grafana_dashboard` | Optional. Ships a bundled Grafana dashboard for `grafana-k8s`. |

## Actions

All actions run the Forgejo CLI inside the workload container as the `git` user.

| Action | Parameters | Result |
| --- | --- | --- |
| `create-admin-user` | `username`, `email` | Creates an admin user with a random password. |
| `generate-user-token` | `username`, `token-name`, `scopes` | Returns an API access token for an existing user. |
| `reset-user-password` | `username`, `password` | Sets the given password on an existing user. |
| `generate-runner-secret` | `name`, `labels`, `scope` | Registers a Forgejo Actions runner and returns its secret. |

Action output (passwords, tokens, runner secrets) is stored in the Juju task log and is
visible with `juju show-task`; treat it as sensitive and rotate or change it after use.
`reset-user-password` takes the password as a plain parameter, so it is also visible there.

## Configuration

Config option names follow the pattern `<forgejo-section>-<key>` in kebab-case
(e.g. `server-domain` sets `[server] DOMAIN`). Options for Forgejo's top-level section
(e.g. `app-name`) have no section prefix.
See the Forgejo
[configuration cheat sheet](https://forgejo.org/docs/latest/admin/config-cheat-sheet/)
for the full meaning of each setting.

Settings not listed below cannot be changed through the charm.

### General and logging

| Option | Default | Description |
| --- | --- | --- |
| `app-name` | `Forgejo` | Instance name shown in the UI. |
| `app-slogan` | `Beyond coding. We Forge.` | Slogan shown below the name. |
| `log-level` | `Info` | One of `Trace`, `Debug`, `Info`, `Warn`, `Error`, `Critical`, `Fatal`, `None`. |
| `proxy-enabled` | `false` | Route Forgejo's outbound HTTP(S) requests through the proxy configured in the model (`juju-http-proxy`, `juju-https-proxy`, `juju-no-proxy`). |

### Server, URLs and SSH

| Option | Default | Description |
| --- | --- | --- |
| `server-domain` | `forgejo.internal` | Domain used to build Forgejo's URL. Must not be empty. With ingress, this is the hostname Traefik routes on. |
| `server-root-url` | `""` | Overrides the public root URL. Empty means `http(s)://<server-domain>/`, with the scheme following the TLS state. |
| `server-disable-ssh` | `false` | Disable Git-over-SSH. |
| `server-ssh-port` | `2222` | SSH port advertised to users in clone URLs. |

### Users, registration and visibility

| Option | Default | Description |
| --- | --- | --- |
| `service-disable-registration` | `false` | Disable self-registration. |
| `service-require-signin-view` | `false` | Require sign-in to view any page. |
| `service-allow-only-external-registration` | `false` | Only allow registration via OAuth/OpenID. |
| `service-register-email-confirm` | `false` | Require email confirmation to activate accounts (needs `mailer-enabled`). |
| `service-register-manual-confirm` | `false` | Require manual admin approval of new accounts. |
| `service-enable-notify-mail` | `false` | Send email notifications for issue and pull-request activity (needs `mailer-enabled`). |
| `service-default-user-visibility` | `public` | Default visibility of new users: `public`, `limited` or `private`. |
| `service-default-org-visibility` | `public` | Default visibility of new organizations: `public`, `limited` or `private`. |
| `service-default-allow-create-organization` | `true` | Let new users create organizations. |
| `admin-disable-regular-org-creation` | `false` | Only admins may create organizations. |
| `admin-user-disabled-features` | `""` | Features disabled for users: `deletion`, `manage_ssh_keys`, `manage_gpg_keys`, `manage_password`. |
| `admin-external-user-disabled-features` | `""` | Same, for users authenticated via OpenID/OAuth2 only. |
| `openid-whitelisted-uris` | `""` | Comma-separated OpenID URIs allowed for sign-in/sign-up. Empty means no restriction. |
| `oauth2-enabled` | `true` | Enable Forgejo as an OAuth2 provider. |
| `session-provider` | `file` | Session backend: `file`, `db`, `memory`. |

### Explore pages

| Option | Default | Description |
| --- | --- | --- |
| `explore-disable-users-page` | `false` | Disable the users explore page. |
| `explore-disable-organizations-page` | `false` | Disable the organizations explore page. |
| `explore-disable-code-page` | `false` | Disable the code explore page. |

### Features

| Option | Default | Description |
| --- | --- | --- |
| `mailer-enabled` | `false` | Enable the mailer. |
| `packages-enabled` | `true` | Enable the package registry. |
| `metrics-enabled` | `true` | Expose `/metrics`. |
| `migrations-allowed-domains` | `""` | Comma-separated hosts repositories may be migrated from (wildcards supported). Empty allows all. |

### Secrets

These options take a Juju secret whose content has a `value` key. Grant the secret to the
application and pass its ID:

| Option | Forgejo setting |
| --- | --- |
| `security-secret-key` | `[security] SECRET_KEY` |
| `security-internal-token` | `[security] INTERNAL_TOKEN` |
| `server-lfs-jwt-secret` | `[server] LFS_JWT_SECRET` |
| `metrics-token` | `[metrics] TOKEN`; also used as the bearer token in the Prometheus scrape job. |

A secret that cannot be read, or has no `value` key, is skipped and Forgejo falls back to its
own behaviour for that setting. Changes to the secret content are picked up automatically.

## Known limitations and deviations from non-charmed Forgejo

* Only PostgreSQL is supported as a database backend (via the `postgresql_client` interface);
  SQLite/MySQL are not wired up.
* Only a subset of Forgejo's settings is exposed as charm config (see above).
* `server-ssh-port` only changes the port advertised to users; the server listens on 2222
  internally and the charm exposes Git-over-SSH through Traefik.
* Configuration changes are applied by rewriting Forgejo's `app.ini` file and replanning the
  Pebble service; some settings may require a Forgejo restart to fully take effect (handled
  automatically by the charm, but there is a short window of unavailability).
* The charm does not manage Forgejo backups/restores, database migrations beyond what
  Forgejo performs itself on startup, or multi-unit/HA deployments (Forgejo itself has no
  built-in clustering).

For workload-specific behaviour not covered here, see the
[Forgejo documentation](https://forgejo.org/docs/latest/).
