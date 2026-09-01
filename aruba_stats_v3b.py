#!/usr/bin/env python3

import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
import urllib3


# ======================================================================
# Defaults
# ======================================================================

DEFAULT_RANGE = 300
DEFAULT_TOP = 0

GRAYLOG_DEFAULT_URL = (
    "https://server.logging/api/search/aggregate"
)

MACLOOKUP_URL = "https://api.maclookup.app/v2/macs"

DEFAULT_CACHE_DB = (
    Path.home()
    / ".cache"
    / "aruba_stats"
    / "mac_cache.db"
)


# ======================================================================
# Negative lookup cache TTLs
# ======================================================================

TTL_NOT_FOUND = timedelta(hours=24)
TTL_RATE_LIMITED = timedelta(minutes=5)
TTL_UNAUTHORIZED = timedelta(hours=1)
TTL_LOOKUP_ERROR = timedelta(minutes=5)
TTL_INVALID_RESPONSE = timedelta(minutes=5)
TTL_OTHER_HTTP_ERROR = timedelta(minutes=5)


# ======================================================================
# Environment
# ======================================================================

def get_required_env(name: str) -> str:

    value = os.getenv(name)

    if not value:

        print(
            f"ERROR: environment variable {name} is not set.",
            file=sys.stderr,
        )

        sys.exit(1)

    return value


def get_tls_verify():

    """
    GRAYLOG_CA=/path/to/ca.pem
        Use CA certificate.

    GRAYLOG_VERIFY=false
        Disable TLS verification.
    """

    ca_file = os.getenv("GRAYLOG_CA")

    if ca_file:
        return ca_file

    verify_env = os.getenv(
        "GRAYLOG_VERIFY",
        "true",
    ).lower()

    if verify_env in (
        "false",
        "0",
        "no",
    ):

        urllib3.disable_warnings(
            urllib3.exceptions.InsecureRequestWarning
        )

        return False

    return True


# ======================================================================
# Time helpers
# ======================================================================

def get_local_timezone() -> timezone:

    """
    Get local timezone from the operating system.
    """

    try:

        localtime = Path("/etc/localtime")

        if localtime.is_symlink():

            target = localtime.resolve()
            target_str = str(target)

            marker = "/zoneinfo/"

            if marker in target_str:

                zone_name = target_str.split(
                    marker,
                    1,
                )[1]

                return ZoneInfo(zone_name)

    except (
        OSError,
        ZoneInfoNotFoundError,
    ):

        pass

    local_now = datetime.now().astimezone()

    return local_now.tzinfo


def parse_hhmm(value: str) -> Tuple[int, int]:

    """
    Parse HH:MM.
    """

    try:

        hour_str, minute_str = value.split(
            ":",
            1,
        )

        hour = int(hour_str)
        minute = int(minute_str)

    except (
        ValueError,
        AttributeError,
    ):

        raise argparse.ArgumentTypeError(
            f"Invalid time '{value}'. Use HH:MM."
        )

    if not 0 <= hour <= 23:

        raise argparse.ArgumentTypeError(
            f"Invalid hour in '{value}'."
        )

    if not 0 <= minute <= 59:

        raise argparse.ArgumentTypeError(
            f"Invalid minute in '{value}'."
        )

    return hour, minute


def build_absolute_timerange(
    date_string: str,
    from_time: str,
    to_time: str,
) -> Tuple[datetime, datetime]:

    """
    Build absolute timerange using local timezone.

    Example:

        --date 2026-08-13
        --from-time 18:07
        --to-time 23:59
    """

    try:

        year, month, day = map(
            int,
            date_string.split("-"),
        )

    except ValueError:

        raise ValueError(
            "Date must be YYYY-MM-DD."
        )

    from_hour, from_minute = parse_hhmm(
        from_time
    )

    to_hour, to_minute = parse_hhmm(
        to_time
    )

    tz = get_local_timezone()

    start = datetime(
        year,
        month,
        day,
        from_hour,
        from_minute,
        0,
        tzinfo=tz,
    )

    end = datetime(
        year,
        month,
        day,
        to_hour,
        to_minute,
        59,
        999999,
        tzinfo=tz,
    )

    if end < start:

        raise ValueError(
            "End time must not be earlier than start time."
        )

    return start, end


def datetime_to_graylog(
    value: datetime,
) -> str:

    """
    Convert datetime to ISO-8601 string.
    """

    return value.isoformat()


# ======================================================================
# SQLite cache
# ======================================================================

def utc_now() -> datetime:

    return datetime.now(
        timezone.utc
    )


def dt_to_db(
    value: Optional[datetime],
) -> Optional[str]:

    if value is None:
        return None

    return value.astimezone(
        timezone.utc
    ).isoformat()


def db_to_dt(
    value: Optional[str],
) -> Optional[datetime]:

    if value is None:
        return None

    try:

        return datetime.fromisoformat(
            value
        )

    except ValueError:

        return None


def init_cache(
    db_path: Path,
) -> sqlite3.Connection:

    """
    Create cache or migrate old schema.

    Older versions of the script did not have expires_at.
    """

    db_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        db_path
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS mac_cache (
            mac TEXT PRIMARY KEY,
            vendor TEXT NOT NULL,
            is_randomized TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    cursor = connection.execute(
        "PRAGMA table_info(mac_cache)"
    )

    columns = {
        row[1]
        for row in cursor.fetchall()
    }

    if "expires_at" not in columns:

        connection.execute(
            """
            ALTER TABLE mac_cache
            ADD COLUMN expires_at TEXT
            """
        )

    connection.commit()

    return connection


def cache_get(
    connection: sqlite3.Connection,
    mac: str,
) -> Optional[Tuple[str, str]]:

    cursor = connection.execute(
        """
        SELECT
            vendor,
            is_randomized,
            expires_at
        FROM mac_cache
        WHERE mac = ?
        """,
        (mac,),
    )

    row = cursor.fetchone()

    if row is None:
        return None

    vendor = row[0]
    randomized = row[1]
    expires_at = db_to_dt(row[2])

    # Permanent cache entry.

    if expires_at is None:

        return vendor, randomized

    # Temporary cache entry is still valid.

    if expires_at > utc_now():

        return vendor, randomized

    # Expired entry.

    connection.execute(
        """
        DELETE FROM mac_cache
        WHERE mac = ?
        """,
        (mac,),
    )

    connection.commit()

    return None


def cache_put(
    connection: sqlite3.Connection,
    mac: str,
    vendor: str,
    randomized: str,
    ttl: Optional[timedelta],
) -> None:

    expires_at = None

    if ttl is not None:

        expires_at = (
            utc_now()
            + ttl
        )

    connection.execute(
        """
        INSERT INTO mac_cache (
            mac,
            vendor,
            is_randomized,
            expires_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?)

        ON CONFLICT(mac)
        DO UPDATE SET
            vendor = excluded.vendor,
            is_randomized = excluded.is_randomized,
            expires_at = excluded.expires_at,
            updated_at = excluded.updated_at
        """,
        (
            mac,
            vendor,
            randomized,
            dt_to_db(expires_at),
            dt_to_db(utc_now()),
        ),
    )

    connection.commit()


# ======================================================================
# Graylog API
# ======================================================================

def query_graylog(
    url: str,
    token: str,
    event_type: str,
    timerange: dict,
    verify,
) -> dict:

    query = (
        f'aruba_event_type:"{event_type}" '
        "AND aruba_severity:error "
        "AND _exists_:aruba_client_mac "
        'AND NOT aruba_client_mac:""'
    )

    payload = {
        "query": query,

        "timerange": timerange,

        "group_by": [
            {
                "field": "aruba_ap",
            },
            {
                "field": "aruba_client_mac",
            },
        ],

        "metrics": [
            {
                "function": "count",
            }
        ],
    }

    response = requests.post(
        url,
        json=payload,
        auth=(
            token,
            "token",
        ),
        headers={
            "X-Requested-By": "aruba-stats",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        verify=verify,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


# ======================================================================
# MAC helpers
# ======================================================================

def normalize_mac(
    mac: str,
) -> str:

    value = (
        mac.strip()
        .replace("-", ":")
        .replace(".", "")
        .upper()
    )

    if (
        ":" not in value
        and len(value) == 12
    ):

        value = ":".join(
            value[i:i + 2]
            for i in range(
                0,
                12,
                2,
            )
        )

    return value


def get_mac_oui(
    mac: str,
) -> str:

    """
    Return only the first three octets.

    This is the only form of a client MAC
    that is sent to the external MAC lookup API.
    """

    normalized = normalize_mac(
        mac
    )

    parts = normalized.split(":")

    if len(parts) >= 3:

        return ":".join(
            parts[:3]
        )

    return normalized[:8]


def is_locally_administered(
    mac: str,
) -> bool:

    """
    Determine whether the MAC address is
    locally administered (LAA).

    The second-least-significant bit of the
    first octet indicates local administration.

    Example:

        66 = 0110 0110

    The LAA bit is set.
    """

    normalized = normalize_mac(
        mac
    )

    parts = normalized.split(":")

    if not parts:
        return False

    try:

        first_octet = int(
            parts[0],
            16,
        )

    except ValueError:

        return False

    return bool(
        first_octet & 0x02
    )


# ======================================================================
# MACLookup
# ======================================================================

def lookup_mac_info(
    mac: str,
    connection: sqlite3.Connection,
    session: requests.Session,
) -> Tuple[str, str]:

    """
    Look up vendor/randomization information.

    IMPORTANT PRIVACY RULE:

    The complete MAC address is NEVER sent to
    the external MACLookup service.

    Example:

        66:1f:c9:77:7e:bc

    becomes:

        66:1f:c9

    before an external request.

    Locally administered addresses are detected
    locally and are not sent to MACLookup at all.
    """

    normalized = normalize_mac(
        mac
    )

    # --------------------------------------------------------------
    # Check local SQLite cache first
    # --------------------------------------------------------------

    cached = cache_get(
        connection,
        normalized,
    )

    if cached is not None:

        return cached

    # --------------------------------------------------------------
    # Detect LAA locally
    # --------------------------------------------------------------

    if is_locally_administered(
        normalized
    ):

        vendor = "Locally administered"
        randomized = "YES"

        cache_put(
            connection,
            normalized,
            vendor,
            randomized,
            None,
        )

        return vendor, randomized

    # --------------------------------------------------------------
    # Only the OUI is allowed to leave the machine.
    # --------------------------------------------------------------

    oui = get_mac_oui(
        normalized
    )

    url = (
        f"{MACLOOKUP_URL}/{oui}"
    )

    try:

        response = session.get(
            url,
            timeout=10,
        )

    except requests.RequestException:

        vendor = "Lookup error"
        randomized = "UNKNOWN"

        cache_put(
            connection,
            normalized,
            vendor,
            randomized,
            TTL_LOOKUP_ERROR,
        )

        return vendor, randomized

    # --------------------------------------------------------------
    # Successful lookup
    # --------------------------------------------------------------

    if response.status_code == 200:

        try:

            data = response.json()

        except ValueError:

            vendor = "Invalid response"
            randomized = "UNKNOWN"

            cache_put(
                connection,
                normalized,
                vendor,
                randomized,
                TTL_INVALID_RESPONSE,
            )

            return vendor, randomized

        vendor = (
            data.get("company")
            or "Unknown"
        )

        is_rand = data.get(
            "isRand"
        )

        if is_rand is True:

            randomized = "YES"

        elif is_rand is False:

            randomized = "NO"

        else:

            randomized = "UNKNOWN"

        # Successful results never expire.

        cache_put(
            connection,
            normalized,
            vendor,
            randomized,
            None,
        )

        return vendor, randomized

    # --------------------------------------------------------------
    # MAC/OUI not found
    # --------------------------------------------------------------

    if response.status_code == 404:

        vendor = "Unknown"
        randomized = "UNKNOWN"

        cache_put(
            connection,
            normalized,
            vendor,
            randomized,
            TTL_NOT_FOUND,
        )

        return vendor, randomized

    # --------------------------------------------------------------
    # Rate limit
    # --------------------------------------------------------------

    if response.status_code == 429:

        vendor = "Rate limited"
        randomized = "UNKNOWN"

        cache_put(
            connection,
            normalized,
            vendor,
            randomized,
            TTL_RATE_LIMITED,
        )

        return vendor, randomized

    # --------------------------------------------------------------
    # Authorization failure
    # --------------------------------------------------------------

    if response.status_code == 401:

        vendor = "Unauthorized"
        randomized = "UNKNOWN"

        cache_put(
            connection,
            normalized,
            vendor,
            randomized,
            TTL_UNAUTHORIZED,
        )

        return vendor, randomized

    # --------------------------------------------------------------
    # Other HTTP error
    # --------------------------------------------------------------

    vendor = (
        f"HTTP {response.status_code}"
    )

    randomized = "UNKNOWN"

    cache_put(
        connection,
        normalized,
        vendor,
        randomized,
        TTL_OTHER_HTTP_ERROR,
    )

    return vendor, randomized


# ======================================================================
# Statistics
# ======================================================================

def build_stats(rows):

    stats: Dict[
        str,
        Dict[str, int]
    ] = defaultdict(dict)

    for row in rows:

        if len(row) != 3:
            continue

        ap, client, count = row

        if (
            not ap
            or ap == "(Empty Value)"
        ):

            continue

        if (
            not client
            or client == "(Empty Value)"
        ):

            continue

        try:

            count = int(count)

        except (
            TypeError,
            ValueError,
        ):

            continue

        stats[ap][client] = count

    return stats


# ======================================================================
# Reporting
# ======================================================================

def print_report(
    stats,
    event_type: str,
    top: int,
    cache_connection: sqlite3.Connection,
) -> None:

    if not stats:

        print(
            "No data found."
        )

        return

    # --------------------------------------------------------------
    # AP summary
    # --------------------------------------------------------------

    ap_stats = []

    for ap, clients in stats.items():

        counts = list(
            clients.values()
        )

        total_errors = sum(
            counts
        )

        unique_clients = len(
            clients
        )

        max_per_client = max(
            counts
        )

        avg_per_client = (
            total_errors
            / unique_clients
        )

        ap_stats.append(
            (
                ap,
                total_errors,
                unique_clients,
                max_per_client,
                avg_per_client,
            )
        )

    ap_stats.sort(
        key=lambda item: item[1],
        reverse=True,
    )

    if top > 0:

        ap_stats = ap_stats[:top]

    print()

    print(
        f"{'AP':<20}"
        f"{'Errors':>10}"
        f"{'Clients':>10}"
        f"{'Max/client':>12}"
        f"{'Avg/client':>13}"
    )

    print(
        "-" * 65
    )

    for (
        ap,
        total_errors,
        unique_clients,
        max_per_client,
        avg_per_client,
    ) in ap_stats:

        print(
            f"{ap:<20}"
            f"{total_errors:>10}"
            f"{unique_clients:>10}"
            f"{max_per_client:>12}"
            f"{avg_per_client:>13.1f}"
        )

    # --------------------------------------------------------------
    # Clients
    # --------------------------------------------------------------

    print()

    print(
        "=" * 110
    )

    print(
        f"Clients by AP: {event_type}"
    )

    print(
        "=" * 110
    )

    session = requests.Session()

    try:

        for ap, _, _, _, _ in ap_stats:

            print()
            print(ap)

            clients = stats[ap]

            sorted_clients = sorted(
                clients.items(),
                key=lambda item: item[1],
                reverse=True,
            )

            if top > 0:

                sorted_clients = (
                    sorted_clients[:top]
                )

            for client, count in sorted_clients:

                vendor, randomized = (
                    lookup_mac_info(
                        client,
                        cache_connection,
                        session,
                    )
                )

                print(
                    f"  {client:<20}"
                    f"{count:>7}"
                    f"    {vendor:<30}"
                    f"{randomized:>10}"
                )

    finally:

        session.close()


# ======================================================================
# Main
# ======================================================================

def main() -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Generic Aruba statistics collector "
            "using Graylog Search API. "
            "External MAC lookups are anonymized "
            "to the first three MAC octets."
        )
    )

    # --------------------------------------------------------------
    # Event type
    # --------------------------------------------------------------

    parser.add_argument(
        "--event-type",
        required=True,
        help=(
            "Aruba event type, e.g. "
            "recv_sta_lkup_req, runtime_error, "
            "wpa3_replay_counter_mismatch"
        ),
    )

    # --------------------------------------------------------------
    # Relative range
    # --------------------------------------------------------------

    parser.add_argument(
        "--range",
        type=int,
        default=DEFAULT_RANGE,
        help=(
            "Relative time range in seconds. "
            f"Default: {DEFAULT_RANGE}."
        ),
    )

    # --------------------------------------------------------------
    # Absolute range
    # --------------------------------------------------------------

    parser.add_argument(
        "--date",
        help=(
            "Date for absolute search period: "
            "YYYY-MM-DD. Used together with "
            "--from-time and --to-time."
        ),
    )

    parser.add_argument(
        "--from-time",
        help=(
            "Start time for absolute search period: "
            "HH:MM."
        ),
    )

    parser.add_argument(
        "--to-time",
        help=(
            "End time for absolute search period: "
            "HH:MM."
        ),
    )

    # --------------------------------------------------------------
    # Top
    # --------------------------------------------------------------

    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        help=(
            "Limit number of APs and clients shown. "
            "0 = no limit."
        ),
    )

    # --------------------------------------------------------------
    # SQLite
    # --------------------------------------------------------------

    parser.add_argument(
        "--cache-db",
        type=Path,
        default=DEFAULT_CACHE_DB,
        help=(
            "SQLite cache database. "
            f"Default: {DEFAULT_CACHE_DB}"
        ),
    )

    # --------------------------------------------------------------
    # Graylog URL
    # --------------------------------------------------------------

    parser.add_argument(
        "--url",
        default=None,
        help=(
            "Override GRAYLOG_URL environment variable."
        ),
    )

    args = parser.parse_args()

    # ==================================================================
    # Validate absolute-time arguments
    # ==================================================================

    absolute_arguments_used = any(
        value is not None
        for value in (
            args.date,
            args.from_time,
            args.to_time,
        )
    )

    if absolute_arguments_used:

        if not (
            args.date
            and args.from_time
            and args.to_time
        ):

            print(
                "ERROR: --date, --from-time and "
                "--to-time must be specified together.",
                file=sys.stderr,
            )

            return 1

        try:

            start_dt, end_dt = (
                build_absolute_timerange(
                    args.date,
                    args.from_time,
                    args.to_time,
                )
            )

        except ValueError as exc:

            print(
                f"ERROR: {exc}",
                file=sys.stderr,
            )

            return 1

        timerange = {
            "type": "absolute",

            "from": datetime_to_graylog(
                start_dt
            ),

            "to": datetime_to_graylog(
                end_dt
            ),
        }

        timerange_description = (
            f"{start_dt.isoformat()} "
            f"→ "
            f"{end_dt.isoformat()}"
        )

    else:

        timerange = {
            "type": "relative",
            "range": args.range,
        }

        timerange_description = (
            f"last {args.range} seconds"
        )

    # ==================================================================
    # Environment
    # ==================================================================

    graylog_url = (
        args.url
        if args.url
        else get_required_env(
            "GRAYLOG_URL"
        )
    )

    graylog_token = get_required_env(
        "GRAYLOG_TOKEN"
    )

    verify = get_tls_verify()

    # ==================================================================
    # Query
    # ==================================================================

    query = (
        f'aruba_event_type:"{args.event_type}" '
        "AND aruba_severity:error "
        "AND _exists_:aruba_client_mac "
        'AND NOT aruba_client_mac:""'
    )

    print(
        f"Query:       {query}"
    )

    print(
        f"Time range:  {timerange_description}"
    )

    print(
        f"Cache DB:    {args.cache_db}"
    )

    print(
        "MAC lookup:  anonymized / first 3 octets only"
    )

    # ==================================================================
    # Graylog
    # ==================================================================

    try:

        result = query_graylog(
            url=graylog_url,
            token=graylog_token,
            event_type=args.event_type,
            timerange=timerange,
            verify=verify,
        )

    except requests.HTTPError as exc:

        print(
            f"Graylog HTTP error: {exc}",
            file=sys.stderr,
        )

        if exc.response is not None:

            print(
                exc.response.text,
                file=sys.stderr,
            )

        return 1

    except requests.RequestException as exc:

        print(
            f"Graylog API error: {exc}",
            file=sys.stderr,
        )

        return 1

    # ==================================================================
    # Effective timerange
    # ==================================================================

    metadata = result.get(
        "metadata",
        {}
    )

    effective_timerange = metadata.get(
        "effective_timerange"
    )

    if effective_timerange:

        print()

        print(
            "Effective timerange:"
        )

        print(
            f"  From: "
            f"{effective_timerange.get('from')}"
        )

        print(
            f"  To:   "
            f"{effective_timerange.get('to')}"
        )

    # ==================================================================
    # Data
    # ==================================================================

    rows = result.get(
        "datarows",
        []
    )

    if not rows:

        print()

        print(
            "No matching events found."
        )

        return 0

    stats = build_stats(
        rows
    )

    if not stats:

        print()

        print(
            "No valid AP/client data found."
        )

        return 0

    # ==================================================================
    # SQLite
    # ==================================================================

    try:

        cache_connection = init_cache(
            args.cache_db
        )

    except sqlite3.Error as exc:

        print(
            f"SQLite error: {exc}",
            file=sys.stderr,
        )

        return 1

    try:

        print_report(
            stats=stats,
            event_type=args.event_type,
            top=args.top,
            cache_connection=cache_connection,
        )

    finally:

        cache_connection.close()

    return 0


# ======================================================================
# Entry point
# ======================================================================

if __name__ == "__main__":

    raise SystemExit(
        main()
    )
