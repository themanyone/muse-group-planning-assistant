"""The internal CA (roadmap §6A.4, B-163).

Split deployments authenticate their processes to each other
with TLS, and the certificates come from an INTERNAL CA the
deployment itself mints — no public CA ever learns this
system's shape, and no cost or external dependency stands
between an install and encrypted transport.

This module mints: one self-signed CA, and leaf certificates
per process (server leaves carry the loopback names/IPs a
local deployment binds; client leaves carry the module's
name as their identity). Validity is long (the CA outlives
any single install's patience) — rotation is B-169's subject;
this module's job is that minting is a library call, testable
and identical everywhere.
"""

from __future__ import annotations

import datetime
import ipaddress

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

# The CA's lifetime: a decade. Leaves: two years, renewable by
# re-issue (B-169 automates the calendar; re-issuing by hand
# is one call here).
_CA_DAYS = 3650
_LEAF_DAYS = 730


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _key() -> rsa.RSAPrivateKey:
    # RSA-2048: universally accepted by TLS stacks on every
    # Tier-1 target, fast enough at this call rate.
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def key_pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def cert_pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def load_cert(pem: bytes) -> x509.Certificate:
    return x509.load_pem_x509_certificate(pem)


def load_key(pem: bytes) -> rsa.RSAPrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    assert isinstance(key, rsa.RSAPrivateKey)
    return key


def create_ca(common_name: str = "gpa-internal-ca") -> tuple[bytes, bytes]:
    """Mint the internal CA; returns (cert_pem, key_pem).

    The key belongs in the deployment's secret store beside
    the other keys — never in the repo, never in the
    database."""
    key = _key()
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(_name(common_name))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=_CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return cert_pem(cert), key_pem(key)


def issue_leaf(
    ca_cert_pem: bytes,
    ca_key_pem: bytes,
    common_name: str,
    *,
    server: bool,
) -> tuple[bytes, bytes]:
    """Mint a leaf under the CA; returns (cert_pem, key_pem).

    Server leaves are valid for the loopback names a local
    deployment actually binds (localhost, 127.0.0.1, ::1) —
    the transport is local by design (§6A.4), so the leaf
    names exactly that world and no other."""
    ca_cert = load_cert(ca_cert_pem)
    ca_key = load_key(ca_key_pem)
    key = _key()
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=_LEAF_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.SERVER_AUTH]
                if server
                else [ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            critical=False,
        )
    )
    if server:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.IPAddress(ipaddress.ip_address("::1")),
                ]
            ),
            critical=False,
        )
    cert = builder.sign(ca_key, hashes.SHA256())
    return cert_pem(cert), key_pem(key)


__all__ = [
    "cert_pem",
    "create_ca",
    "issue_leaf",
    "key_pem",
    "load_cert",
    "load_key",
]
