<!--
Avoid using this README file for information that is maintained or published elsewhere, e.g.:

* metadata.yaml > published on Charmhub
* documentation > published on (or linked to from) Charmhub
* detailed contribution guide > documentation or CONTRIBUTING.md

Use links instead.
-->

# forgejo-k8s-operator

Charmed k8s operator for forgejo.


## Expected to be used with

* Postgresql (or pgbouncer) for the database backend
* Traefik for ingress

Example deloyment:

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

