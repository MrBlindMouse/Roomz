"""Clock sync: server provides UTC timestamp for NTP-style client offset calculation.

Server does not store state; it replies to sync requests with server_utc and
echoes client_time so the client can compute:
  rtt = performance.now() - client_time
  offset_ms = (server_utc * 1000) - (client_time + rtt/2)
"""

import time
from typing import Optional


def server_timestamp_utc() -> float:
    """Return current UTC time in seconds (Unix timestamp)."""
    return time.time()


def compute_offset_ms(
    client_sent_time_ms: float,
    server_utc_seconds: float,
    client_receive_time_ms: Optional[float] = None,
) -> float:
    """Compute client clock offset from server in milliseconds.

    If client_receive_time_ms is provided (performance.now() when client got reply),
    then rtt = client_receive_time_ms - client_sent_time_ms and
    offset = server_utc*1000 - (client_sent_time_ms + rtt/2).
    Otherwise assumes one-way: offset = server_utc*1000 - client_sent_time_ms.
    """
    if client_receive_time_ms is not None:
        rtt_ms = client_receive_time_ms - client_sent_time_ms
        half_rtt = rtt_ms / 2
    else:
        half_rtt = 0.0
    server_ms = server_utc_seconds * 1000
    return server_ms - (client_sent_time_ms + half_rtt)
