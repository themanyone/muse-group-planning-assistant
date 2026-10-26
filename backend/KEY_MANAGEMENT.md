# Key management

*Group Planning Assistant — the B-167/B-169 key hierarchy,
in one page. GPLv2.*

## The hierarchy

```
GPA_MASTER_KEY_B64  (environment / keyring — never in the DB)
  └─ wraps ─▶ per-group data keys      (table group_keys)
                 ├─ seal ─▶ sealed fields (source chunks, provenance quotes, event-link quotes)
                 └─ derives ─▶ blind indexes (exact-email lookups over sealed data)
  └─ wraps ─▶ per-file keys            (inside each stored object's GPAE1 header, B-168)
  └─ derives ─▶ the internal-API comms key and per-module signing seeds
                (domain-separated: gpa-internal-comms-v1 / module names)

Recovery codes (shown once at minting — rested NOWHERE)
  └─ scrypt(code, per-code salt) ─▶ seals of the master key (table recovery_escrow)
```

Rules that follow from the shape:

- **The master key is the only irreplaceable secret**, and the
  recovery escrow exists so even it is not: any one unspent code
  restores it. The escrow's row-finding check is keyed by the app
  secret (`GPA_SECRET_KEY`), never by the master — recovery must
  work on the day the master is gone.
- **Data keys never rest unwrapped.** A stolen database yields
  wrapped keys, sealed fields, and escrow seals — ciphertext all
  the way down.
- **Backups contain no usable key material** (B-146 rule, tested
  at B-169): a bundle holds the database (wrapped keys + escrow
  seals only) and storage objects (sealed). Master keys, data
  keys, and recovery codes appear in no bundle, in no encoding.

## Operations

**Rotate the master** (no flag day, sealed data untouched):
set `GPA_MASTER_KEY_B64` to the NEW key and
`GPA_MASTER_KEY_OLD_B64` to the old one, queue the
`crypto.rotate_master` job (or call
`gpa.services.keymgmt.rotate_master_key`), watch it land, then
unset the old key. Rotation re-wraps group keys in one
transaction — it lands whole or not at all.

**Mint recovery codes**: `mint_recovery_codes` returns a set of
eight codes exactly once and deletes any previous set — minting
IS revocation of a lost set. The owner stores them offline (the
Samtal pattern: shown once, emailable once, kept nowhere by the
service).

**Recover**: `recover_master_key` spends one code and returns
the master; install it as `GPA_MASTER_KEY_B64` and restart. A
spent code never works twice; a wrong code teaches nothing.
