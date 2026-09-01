# Aruba Stats

Small Python utility for collecting Aruba Wi-Fi client error statistics from Graylog.

The script queries the Graylog Search API, groups errors by Access Point and client MAC address, and displays:

* total errors per AP;
* number of affected clients;
* maximum and average errors per client;
* client-level error counts;
* MAC vendor information;
* whether a MAC address is locally administered/randomized.

MAC addresses sent to the external MAC lookup service are **anonymized**: only the first three octets (OUI) are sent. Locally administered addresses are detected locally and are not sent externally.

MAC lookup results are cached in a local SQLite database.

## Requirements

* Python 3
* `requests`
* Graylog Search API access
* `maclookup.app` for vendor lookup

Install the Python dependency:

```bash
pip install requests
```

## Configuration

Set the Graylog URL and API token:

```bash
export GRAYLOG_URL="https://logging.server.local/api/search/aggregate"
export GRAYLOG_TOKEN="YOUR_GRAYLOG_TOKEN"
```

Optional TLS settings:

```bash
export GRAYLOG_CA="/path/to/ca.pem"
```

or, if TLS verification must be disabled:

```bash
export GRAYLOG_VERIFY=false
```

## Usage

Basic query using the last 300 seconds:

```bash
./aruba_stats_v3b.py --event-type recv_sta_lkup_req
```

Specify a relative time range:

```bash
./aruba_stats_v3b.py \
  --event-type recv_sta_lkup_req \
  --range 600
```

Specify an absolute time range:

```bash
./aruba_stats_v3b.py \
  --event-type recv_sta_lkup_req \
  --date 2026-08-12 \
  --from-time 21:01 \
  --to-time 23:59
```

Limit the number of APs and clients displayed:

```bash
./aruba_stats_v3b.py \
  --event-type recv_sta_lkup_req \
  --top 10
```

Use a custom SQLite cache:

```bash
./aruba_stats_v3b.py \
  --event-type recv_sta_lkup_req \
  --cache-db /path/to/mac_cache.db
```

Use a different Graylog API URL without changing the environment:

```bash
./aruba_stats_v3b.py \
  --event-type recv_sta_lkup_req \
  --url "https://logging.server.local/api/search/aggregate"
```

## Options

| Option         | Description                                              |
| -------------- | -------------------------------------------------------- |
| `--event-type` | Aruba event type to search for. **Required.**            |
| `--range`      | Relative time range in seconds. Default: `300`.          |
| `--date`       | Date for an absolute search period (`YYYY-MM-DD`).       |
| `--from-time`  | Start time (`HH:MM`).                                    |
| `--to-time`    | End time (`HH:MM`).                                      |
| `--top`        | Maximum number of APs and clients shown. `0` = no limit. |
| `--cache-db`   | Path to the SQLite MAC cache database.                   |
| `--url`        | Override the `GRAYLOG_URL` environment variable.         |
| `--help`       | Show command-line help.                                  |

`--date`, `--from-time` and `--to-time` must be specified together.

## Example output

```text
AP                      Errors   Clients  Max/client   Avg/client
-----------------------------------------------------------------
WiFi/234                  65         9          18          7.2

==============================================================================================================
Clients by AP: recv_sta_lkup_req
==============================================================================================================

WiFi/234
  01:13:02:xx:xx:xx        18    ACTIA                                 NO
  02:f5:31:xx:xx:xx         9    Unknown                              YES
  53:12:51:xx:xx:xx         8    Unknown                              YES
  94:95:ee:xx:xx:xx         8    Unknown                              YES
```

## Privacy

The complete client MAC address is **never sent to the external MAC lookup service**.

For example:

```text
66:1f:c9:77:7e:bc
```

is reduced to:

```text
66:1f:c9
```

before the external request.

Locally administered MAC addresses are identified locally and do not require an external lookup.

## License

MIT License
