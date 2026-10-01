# Test fixtures

`gateway-v1-contract.json` contains only fixed, public, non-production bytes.
Its repeated device key is a cross-language test vector, not a pairing secret
and must never be copied into a real Gateway device store.

`nist-rsa-pkcs1v15-sha256-*` contains one passing 2048-bit RSA PKCS#1 v1.5
SHA-256 signature-verification vector from NIST CAVP
`SigVer15_186-3.rsp`. The source archive is:

```text
https://csrc.nist.gov/CSRC/media/Projects/Cryptographic-Algorithm-Validation-Program/documents/dss/186-3rsatestvectors.zip
SHA-256: 8405aeb3572a4f98ed4b1a3ccb3f2f49e725462dd28ec4759d6a15d88855d19c
```

The response marks this vector `Result = P` and supplies only public modulus,
public exponent, message, and signature (`d = 0`). The PEM file is the public
SubjectPublicKeyInfo representation of that modulus and exponent. These files
are immutable public verification material, not a MOOS release identity or
production trust anchor. No corresponding private key is present or required.

`nist-ecdsa-p256-public.pub` contains the public SubjectPublicKeyInfo encoding
of the first passing P-256 public-key-validation vector in NIST CAVP
`PKV.rsp`:

```text
Qx = e0f7449c5588f24492c338f2bc8f7865f755b958d48edb0f2d0056e50c3fd5b7
Qy = 86d7e9255d0f4b6f44fa2cd6f8ba3c0aa828321d6d8cc430ca6284ce1d5b43a0
Result = P (0 )
```

Its official source archive is:

```text
https://csrc.nist.gov/CSRC/media/Projects/Cryptographic-Algorithm-Validation-Program/documents/dss/186-4ecdsatestvectors.zip
SHA-256: fe47cc92b4cee418236125c9ffbcd9bb01c8c34e74a4ba195d954bcb72824752
```

This fixture contains only the NIST public point on `prime256v1`; no private
scalar is present or required. It verifies that the operator fingerprint path
continues to accept the documented ECDSA public-key form without generating a
cryptographic identity inside the checkout.

`moos-admin-release-v1.tar`, its detached `.sig`, and
`moos-admin-release-v1.pub` form an immutable, externally signed historical
regression fixture for the pre-manifest administrator-release archive format.
The archive was
built from repository commit `9ae7795405885cea46293cbfabe21dc335742b55` with
the normal deterministic builder. Its two Gateway executable members contain
the same fixed test-only ELF bytes used by `tests/admin_release.py`; every other
member contains the repository source bytes selected by `RELEASE_MEMBERS`.

The unsigned archive left the checkout before a one-time, non-production RSA
fixture identity signed it in an isolated external temporary environment. The
private fixture key was destroyed there. It never entered the repository,
checkout tooling, build/output trees, repository-controlled temporary paths,
or CI. Only these public verification artifacts returned:

```text
moos-admin-release-v1.tar
SHA-256: 399de03de8faa07f0cd3bdbf01913bb68a0d2ac533f7c0cea23259ccfbb2f7d2

moos-admin-release-v1.tar.sig
SHA-256: 31a22ab36a29d48e2de9370b50d0d165cea6fe333a00fcc3c6bccea63ee781f1

moos-admin-release-v1.pub
SHA-256: 83165476282c93a07bdeff408860825d69e5462b6cccdd8537560a41be2501da
SubjectPublicKeyInfo DER SHA-256: c9e954a07c6b561f3eb92a5bee6ac7343b05c0e701043dd310b645d69e50fd42
```

The test authenticates the immutable fixture, reconstructs its historical source
only through an explicit legacy regression path, and verifies that the current
builder emits a different canonical-manifest archive. Current-source
deterministic construction and canonical metadata/integrity are tested
separately. The historical fixture is never installable by the normal helper;
it is test evidence, not a production publisher identity or bootstrap trust
anchor.
