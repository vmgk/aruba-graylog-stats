# Changelog

## aruba_stats 3b

### Privacy / MAC address handling

- **Full client MAC addresses are no longer sent to the external MACLookup API.**
- MAC addresses are normalized locally before processing.
- Only the **first three octets (OUI)** are sent to MACLookup for vendor identification.
- The complete MAC address remains local to the machine and is never included in the external MACLookup request.
- Added local detection of **locally administered addresses (LAA)**.
- LAA detection is performed **before any external API request**.
- Locally administered MAC addresses are therefore **not sent to MACLookup at all**.
- LAA addresses are reported locally as:
  - Vendor: `Locally administered`
  - Randomized: `YES`
- Removed the `--anonymize` / `--no-anonymize` command-line switch because anonymized lookup is now the **only supported behavior**.

### SQLite cache

- Cache keys are now based on the **full normalized MAC address**.
- This allows the local cache to retain information specific to each client while keeping the external API request anonymized.
- Existing cache migration mechanism remains unchanged.
- Negative lookup TTLs remain unchanged.

### Reporting

- Added an explicit report message:

  `MAC lookup: anonymized / first 3 octets only`

- Removed the previous runtime indication of selectable anonymization mode.
- The report no longer offers a `FULL MAC` lookup mode.

### Other

- Core Graylog functionality remains unchanged.
- `--range`, `--date`, `--from-time`, `--to-time`, `--top`, `--cache-db` and `--url` remain available.
- Graylog API query, AP/client aggregation and statistics calculation remain unchanged.

---

## aruba_stats 3a

### MAC anonymization

- Added optional MAC address anonymization for external MAC lookups.
- Added `--anonymize` option.
- Added `--no-anonymize` option for cases where a full MAC lookup is explicitly required.
- **Anonymization is enabled by default.**

When anonymization is enabled:

```text
66:1f:c9:77:7e:bc
```

is sent to MACLookup as:

```text
66:1f:c9
```

Only the OUI is exposed to the external service.

### MACLookup behavior

- Added `get_oui()` helper for extracting the first three MAC octets.
- MACLookup can operate in two modes:
  - anonymized: OUI only
  - non-anonymized: complete MAC
- `isRand` information from MACLookup is available only when the complete MAC is sent.
- In anonymized mode, randomization status is reported as `UNKNOWN`, because the OUI alone cannot reliably determine whether an individual address is randomized.

### SQLite cache

- Cache entries are keyed according to the value used for the external lookup.
- Existing automatic schema migration and expiration handling remain available.
- Successful lookups are cached permanently.
- Negative/error results continue to use temporary TTLs.

### Reporting

- Added an explicit indication of the active MAC lookup mode:

```text
MACLookup mode: ANONYMIZED (only OUI is sent externally)
```

or:

```text
MACLookup mode: FULL MAC
```

### Existing functionality retained

- Graylog Search API aggregation.
- AP/client statistics.
- Relative time range using `--range`.
- Absolute time range using:
  - `--date`
  - `--from-time`
  - `--to-time`
- `--top` filtering.
- SQLite cache.
- Automatic cache schema migration.
- TLS configuration through `GRAYLOG_CA` / `GRAYLOG_VERIFY`.
- Graylog URL override through `--url`.

---

## aruba_stats 3

### Initial release of the current architecture

- Added Graylog Search API aggregation for Aruba event statistics.
- Added filtering by Aruba event type.
- Added filtering for error severity and valid client MAC addresses.
- Added grouping by:
  - `aruba_ap`
  - `aruba_client_mac`
- Added AP-level statistics:
  - total errors
  - unique clients
  - maximum errors per client
  - average errors per client
- Added per-client reporting.

### Time range support

- Added relative time range with `--range`.
- Added absolute time ranges using:
  - `--date`
  - `--from-time`
  - `--to-time`
- Added local timezone handling through the system timezone configuration.
- Added ISO-8601 timestamps for Graylog absolute searches.

### SQLite caching

- Added persistent SQLite cache for MACLookup results.
- Added automatic cache database creation.
- Added automatic migration of older cache schemas.
- Added cache expiration support through `expires_at`.
- Added temporary caching for:
  - not found results
  - rate limiting
  - authorization failures
  - lookup errors
  - invalid API responses
  - other HTTP errors
- Successful MACLookup results are cached permanently.

### MACLookup integration

- Added vendor lookup through the MACLookup API.
- Added detection of the API's `isRand` field.
- Added handling for HTTP 404, 429, 401 and other HTTP errors.
- Added request timeout and exception handling.

### CLI

Initial command-line options included:

- `--event-type`
- `--range`
- `--date`
- `--from-time`
- `--to-time`
- `--top`
- `--cache-db`
- `--url`

### Graylog / TLS

- Added Graylog authentication using `GRAYLOG_TOKEN`.
- Added configurable Graylog URL through `GRAYLOG_URL`.
- Added optional `--url` override.
- Added configurable TLS verification using:
  - `GRAYLOG_CA`
  - `GRAYLOG_VERIFY`

---

## Version evolution summary

| Version | MAC sent externally | LAA detected locally | Anonymization selectable | Main change |
|---|---|---:|---:|---|
| **3** | Full MAC | No | No | Initial Graylog statistics + MACLookup + SQLite cache |
| **3a** | OUI by default, full MAC optionally | No | Yes | Introduced MAC anonymization |
| **3b** | **OUI only** | **Yes** | **No** | Privacy-by-default redesign; full MAC never leaves the machine |

### Privacy evolution

The MACLookup privacy model evolved as follows:

```text
v3
  Full MAC
      │
      ▼
  MACLookup API


v3a
  Full MAC
      │
      ├── --no-anonymize ──► MACLookup
      │
      └── default ─────────► OUI only ──► MACLookup


v3b
  Full MAC
      │
      ├── LAA ─────────────► Local handling only
      │
      └── Normal MAC ──────► OUI only ──► MACLookup
```

**Result:** Starting with **3b**, the complete client MAC address is never sent to the external MACLookup service. Only the OUI may leave the local system, and locally administered addresses are handled entirely locally.
