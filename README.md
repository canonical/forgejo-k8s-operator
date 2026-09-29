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

* `create-admin-user` — create a Forgejo admin user with a random password (returned in the
  action output; treat it as sensitive, e.g. rotate it or store it in a Juju secret).
* `generate-user-token` — generate an API access token for an existing Forgejo user.
* `reset-user-password` — set an explicit new password for an existing Forgejo user (the
  `password` parameter, not a random one; it will appear in `juju show-task` output).
* `generate-runner-secret` — generate and register a registration secret for a Forgejo Actions
  runner (globally, or scoped to an owner/repo).

Run `juju run <unit> <action> --help` (or see `charmcraft.yaml`) for parameters and defaults.

## Configuration

Config option names follow the pattern `<forgejo-section>-<key>` in
kebab-case (e.g. `server-domain`, `service-disable-registration`,
`session-provider`), corresponding to the `[section] KEY` they set in
Forgejo's `app.ini`. Options that map to Forgejo's top-level `[DEFAULT]`
section (e.g. `app-name`) have no section prefix.

Run `juju config forgejo-k8s` to see the full list of options, their
descriptions, defaults, and current values. Options of type `secret`
(e.g. `security-secret-key`, `metrics-token`) expect a Juju secret URI —
create one with `juju add-secret` and grant it to the application, or
`juju config forgejo-k8s <option>=secret:<id>`.

## Known limitations and deviations from non-charmed Forgejo

* Only PostgreSQL is supported as a database backend (via the `postgresql_client` interface);
  SQLite/MySQL are not wired up.
* Configuration changes are applied by rewriting Forgejo's `app.ini` file and replanning the
  Pebble service; some settings may require a Forgejo restart to fully take effect (handled
  automatically by the charm, but there is a short window of unavailability).
* The charm does not manage Forgejo backups/restores, database migrations beyond what
  Forgejo performs itself on startup, or multi-unit/HA deployments (Forgejo itself has no
  built-in clustering).

For workload-specific behaviour not covered here, see the
[Forgejo documentation](https://forgejo.org/docs/latest/).
