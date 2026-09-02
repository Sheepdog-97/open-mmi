# Open MMI Independent Trust Checker v1

`open_mmi_trust_check.py` is a standalone, read-only verifier for an installed
Open MMI system. It deliberately does **not** import or execute the installed
Open MMI Python package and does not consume Trust Inspector output.

The checker independently implements the v1 JSON canonicalization, digest,
lineage, inventory and manifest vocabulary rules. It uses the checker's own
`/usr/bin/git` and `/usr/bin/gpg` only to inspect Git objects and verify the
integrity-bound commit against the pinned public key.

## External anchor

A full check requires the owner to supply the expected OpenPGP **primary
fingerprint** from an independent source:

```sh
./open_mmi_trust_check.py \
  --expected-signer-fingerprint <FULL_PRIMARY_FINGERPRINT>
```

For a target filesystem mounted from rescue/live media:

```sh
./open_mmi_trust_check.py \
  --target-root /mnt/open-mmi-root \
  --expected-signer-fingerprint <FULL_PRIMARY_FINGERPRINT>
```

If the managed Git repository is not available at the path recorded by the
target's `.update-source.json`, pass a separately mounted repository explicitly:

```sh
./open_mmi_trust_check.py \
  --target-root /mnt/open-mmi-root \
  --repository /mnt/open-mmi-repository \
  --expected-signer-fingerprint <FULL_PRIMARY_FINGERPRINT>
```

Optional independent anchors can bind the checker executable itself and the
current transition-lineage head:

```sh
./open_mmi_trust_check.py \
  --expected-signer-fingerprint <FULL_PRIMARY_FINGERPRINT> \
  --expected-checker-sha256 sha256:<64-lowercase-hex> \
  --expected-lineage-head sha256:<64-lowercase-hex>
```

Use `--json` for machine-readable evidence.

## What v1 verifies

The checker independently validates:

- Trust Manifest v1 vocabulary and canonical digest;
- root-owned accepted owner trust state;
- the transition-lineage hash chain and current accepted-state anchor;
- installed release integrity-state canonicalization and recording anchors;
- the integrity inventory against the exact signed Git commit tree;
- active `/opt/open-mmi` source and site-packages bytes;
- the privileged Python interpreter ownership path;
- deployed privileged system/user unit bytes;
- externally measurable network, persistence and remote-identity systemd
  contracts understood by checker v1;
- the pinned OpenPGP public key against the externally supplied primary
  fingerprint; and
- the integrity-bound Git commit's offline signature.

Unknown future capability contracts are `UNVERIFIED`, not automatically trusted.
Missing evidence is also `UNVERIFIED`; malformed, contradictory or weakened
evidence is `FAIL`.

## Deliberate limit

This commit does not claim an independent physical CAN observation. The separate
CAN trust test owns challenge generation and challenge-bound passive-CAN evidence.
The Open MMI runtime must not gain CAN transmit authority in order to satisfy this
checker.
