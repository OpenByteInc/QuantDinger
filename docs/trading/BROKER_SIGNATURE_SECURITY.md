# Broker signature security review

## PR #411 CodeQL findings

The six new findings are separate from the passing backend tests, repository
checks and Sonar quality gate. CodeQL rules remain enabled. The repository owner
authorized the two protocol-specific dispositions below on 2026-11-27; GitHub
records the individual review comments. Code fixes still require remote scanning
after publishing changes.

| Alert | Assessment and action |
| --- | --- |
| [966](https://github.com/OpenByteInc/QuantDinger/security/code-scanning/966) | Bound dated-contract input before regex matching and bound the root repetition. Long untrusted inputs cannot reach the regex. Only known roots are mapped; continuous contracts are not substituted. |
| [967](https://github.com/OpenByteInc/QuantDinger/security/code-scanning/967), [968](https://github.com/OpenByteInc/QuantDinger/security/code-scanning/968) | Credential errors already had an exact allowlist. Return the canonical static code, never the exception-derived string. Preserve existing validation codes and the generic fallback. |
| [969](https://github.com/OpenByteInc/QuantDinger/security/code-scanning/969) | Advertisement errors must not return arbitrary `ValueError` text. Return static invalid/conflict codes with the existing 400/409 statuses. Other failures retain a generic 500 response. |
| [970](https://github.com/OpenByteInc/QuantDinger/security/code-scanning/970) | Dismissed as a false positive with owner authorization. SHA-256 computes an HTTP content digest, not a password verifier. The documented protocol is unchanged. |
| [971](https://github.com/OpenByteInc/QuantDinger/security/code-scanning/971) | Dismissed as `won't fix` with owner authorization to accept the provider protocol risk. Webull requires MD5(body) within its signing protocol; the cryptographic limitation is not claimed to be fixed. |

## Forthright content digest

The [official signing example](https://dev-openapi-doc.techfx88.com/zh/quick-start/signature.html)
computes SHA-256 over the exact compact JSON bytes, encodes it as `Content-Digest`,
and includes that header in the signed message. This is consistent with the
[HTTP content-digest role](https://www.rfc-editor.org/rfc/rfc9530.html#section-2).
Unlock requests contain a password in that JSON, so a sensitive-data flow reaches
the digest. The digest is neither persisted as a password hash nor compared with
a stored password verifier.

Changing it to bcrypt/Argon2, salting it independently or omitting the password
would no longer sign the bytes required by the broker. HTTPS certificate
verification, signed authority/path/body, nonce/timestamp and disabled redirects
remain in force. Credential storage continues to use the separate encrypted
vault. Neither plaintext unlock bodies nor their digest/signature headers should
be logged or exposed in public responses. Regression tests cover exact unlock
bytes, Ed25519/RSA verification and rejection of a tampered digest.

## Webull version 1.0 signature

The [official signature specification](https://developer.webull.com/apis/docs/authentication/signature/)
requires uppercase MD5(body) in the canonical input and HMAC-SHA1 with the app
secret to produce the signature. This adapter uses the official US HTTPS hosts
and normal certificate verification. Account and order IDs in a request body are
not being converted into password hashes or authentication tokens by MD5 alone.

MD5 is collision-broken; the provider requirement does not make it a suitable
general-purpose integrity or password-storage algorithm. A unilateral SHA-256
replacement would produce invalid signatures, and a different regional API's
signing scheme is not interchangeable. Any algorithm migration must be supported
by the provider and tested against its protocol. Existing compatibility tests
remain enabled, with added checks that ordinary body/account/nonce/timestamp
changes alter the signature. These tests are not a proof of collision resistance
or a live-broker certification.

These dispositions apply only to alerts 970 and 971. No blanket query exclusions,
scanner bypasses or automatic dismissals are introduced. Re-review is required if
either digest is reused for another purpose or the provider offers a compatible
stronger protocol.
