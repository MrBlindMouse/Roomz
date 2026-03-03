"""Clock sync: server provides UTC timestamp for NTP-style client offset calculation.

Server does not store state; it replies to sync requests with server_utc and
echoes client_time so the client can compute:
  rtt = performance.now() - client_time
  offset_ms = (server_utc * 1000) - (client_time + rtt/2)
"""

import time


def server_timestamp_utc() -> float:
    """Return current UTC time in seconds (Unix timestamp)."""
    return time.time()
