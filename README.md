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

Example:

```sh
juju deploy forgejo-k8s
juju deploy postgresql-k8s --channel=14/stable --trust
juju deploy traefik-k8s --config external_hostname=my.internal --config routing_mode=subdomain --trust

juju integrate forgejo-k8s postgresql-k8s
juju integrate forgejo-k8s traefik-k8s
```

```console
Unit               Workload  Agent  Address      Ports  Message
forgejo-k8s/0*     active    idle   10.1.131.36
postgresql-k8s/0*  active    idle   10.1.131.7          Primary
traefik-k8s/0*     active    idle   10.1.131.37         Serving at my.internal
````

```console
# juju run traefik-k8s/0 show-proxied-endpoints | yq .proxied-endpoints | jq .
Running operation 11 with 1 task
  - task 12 on unit-traefik-k8s-0

Waiting for task 12...
{
  "traefik-k8s": {
    "url": "http://my.internal"
  },
  "forgejo-k8s": {
    "url": "http://staging-forgejo-k8s.my.internal/"
  }
}
```

