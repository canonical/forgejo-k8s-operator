# Copyright 2025 Nishant Dash
# See LICENSE file for licensing details.

"""TLS certificate handling for the Forgejo K8s charm."""

import logging
import ops
from typing import Optional

from charms.tls_certificates_interface.v4.tls_certificates import (
    Certificate,
    CertificateRequestAttributes,
    Mode,
    PrivateKey,
    ProviderCertificate,
    TLSCertificatesRequiresV4,
)

logger = logging.getLogger(__name__)

CERTS_DIR_PATH = "/etc/forgejo"
PRIVATE_KEY_NAME = "forgejo.key"
CERTIFICATE_NAME = "forgejo.pem"


class CertHandler:
    """Manages TLS certificates for the Forgejo container."""

    def __init__(self, charm: ops.CharmBase, common_name: str, events: list[ops.BoundEvent]):
        self.charm = charm
        self.csra = CertificateRequestAttributes(
            common_name=common_name, sans_dns=frozenset({common_name})
        )
        self.certificates = TLSCertificatesRequiresV4(
            charm=charm,
            relationship_name="certificates",
            certificate_requests=[self.csra],
            mode=Mode.UNIT,
            refresh_events=events,
        )
        self.container = charm.unit.get_container("forgejo")

    @property
    def cert_path(self) -> str:
        return f"{CERTS_DIR_PATH}/{CERTIFICATE_NAME}"

    @property
    def key_path(self) -> str:
        return f"{CERTS_DIR_PATH}/{PRIVATE_KEY_NAME}"

    def configure_certs(self) -> bool:
        """Write certificate and private key to the container if available.

        Returns True if TLS is ready to use, False otherwise.
        """
        if not self.container.can_connect():
            logger.info("Cannot connect to container, skipping cert configuration")
            return False
        if not self._relation_created("certificates"):
            logger.info("No certificates relation present")
            return False
        if not self._certificate_is_available():
            logger.info("Certificate not yet available from provider")
            return False

        self._check_and_update_certificate()
        return True

    def remove_certs(self) -> None:
        """Remove certificate and private key files from the container."""
        self._remove_certificate()
        self._remove_private_key()

    def _relation_created(self, relation_name: str) -> bool:
        return bool(self.charm.model.relations.get(relation_name))

    def _certificate_is_available(self) -> bool:
        cert, key = self.certificates.get_assigned_certificate(
            certificate_request=self.csra
        )
        return bool(cert and key)

    def _check_and_update_certificate(self) -> bool:
        """Check if certificate or key needs updating and write them if so."""
        provider_certificate, private_key = self.certificates.get_assigned_certificate(
            certificate_request=self.csra
        )
        if not provider_certificate or not private_key:
            logger.debug("Certificate or private key not available")
            return False

        certificate_updated = False
        private_key_updated = False

        if self._is_certificate_update_required(provider_certificate.chain):
            self._store_certificate(provider_certificate)
            certificate_updated = True

        if self._is_private_key_update_required(private_key):
            self._store_private_key(private_key)
            private_key_updated = True

        return certificate_updated or private_key_updated

    def _is_certificate_update_required(self, certs: list[Certificate]) -> bool:
        return self._get_existing_certificate() != self._concat_chain(certs)

    def _is_private_key_update_required(self, private_key: PrivateKey) -> bool:
        return self._get_existing_private_key() != private_key

    def _get_existing_certificate(self) -> Optional[str]:
        return self._get_stored_certificate() if self._certificate_is_stored() else None

    def _get_existing_private_key(self) -> Optional[PrivateKey]:
        return self._get_stored_private_key() if self._private_key_is_stored() else None

    def _certificate_is_stored(self) -> bool:
        return self.container.exists(path=self.cert_path)

    def _private_key_is_stored(self) -> bool:
        return self.container.exists(path=self.key_path)

    def _get_stored_certificate(self) -> str:
        return str(self.container.pull(path=self.cert_path).read())

    def _get_stored_private_key(self) -> PrivateKey:
        key_string = str(self.container.pull(path=self.key_path).read())
        return PrivateKey.from_string(key_string)

    def _store_certificate(self, certificate: ProviderCertificate) -> None:
        self.container.push(
            path=self.cert_path,
            source=self._concat_chain(certificate.chain),
            make_dirs=True,
            user_id=1000,
            user="git",
            group_id=1000,
            group="git",
        )
        logger.info("Pushed certificate to workload")

    def _store_private_key(self, private_key: PrivateKey) -> None:
        self.container.push(
            path=self.key_path,
            source=str(private_key),
            make_dirs=True,
            user_id=1000,
            user="git",
            group_id=1000,
            group="git",
        )
        logger.info("Pushed private key to workload")

    def _remove_certificate(self) -> None:
        if self._certificate_is_stored():
            self.container.exec(["rm", self.cert_path]).wait()

    def _remove_private_key(self) -> None:
        if self._private_key_is_stored():
            self.container.exec(["rm", self.key_path]).wait()

    @staticmethod
    def _concat_chain(certs: list[Certificate]) -> str:
        return "\n".join([str(c) for c in certs])
