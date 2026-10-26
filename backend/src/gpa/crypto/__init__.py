"""Field-level encryption (roadmap B-167).

The envelope scheme, in one package:

* A **master key** — from the environment (``GPA_MASTER_KEY_B64``,
  an OS keyring in deployments that have one), NEVER in the
  database. Development's documented fallback derives it from
  the app secret under a domain label, the same posture as
  the other keys in this system.
* **Per-group data keys** — random 32-byte keys, wrapped
  under the master key and stored in ``group_keys``. A copied
  database yields neither the fields (sealed under data keys)
  nor the data keys (sealed under a key it has never seen).
* **Sealed fields** — AES-256-GCM under the group's data key,
  stored as ``enc:v1:`` + base64, the version prefix riding
  so algorithms can rotate (the Samtal-proven pattern). The
  authenticated data binds each seal to its group AND field,
  so a blob replayed into another field or group does not
  open. Reads tolerate legacy plaintext (no prefix) so the
  upgrade strands nothing.
* A **keyed blind index** — HMAC-SHA256 under a dedicated
  index key derived from the master — for the fields that
  must stay lookup-able beside their sealed form.

Settings resolve exactly like the job chassis' (B-166's
one-source law): an explicit argument, else the injected
process settings, else the environment's — never two sources
in one process.
"""
