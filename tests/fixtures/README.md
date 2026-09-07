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
