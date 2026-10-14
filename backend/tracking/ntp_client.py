import datetime
import socket
import struct
import time
from typing import Dict, Optional
from zoneinfo import ZoneInfo


class NTPClient:
    """
    Queries Network Time Protocol (NTP) servers to maintain accurate time
    with drift offset compensation. Falls back to system monotonic time if NTP is unreachable.
    """

    DEFAULT_SERVERS = [
        "pool.ntp.org",
        "time.google.com",
        "time.cloudflare.com",
        "time.windows.com"
    ]
    # Difference between NTP epoch (1900-01-01) and Unix epoch (1970-01-01)
    NTP_DELTA = 2208988800

    def __init__(self, server: str = "pool.ntp.org", timeout: float = 2.0):
        self.server = server
        self.timeout = timeout
        self.offset: float = 0.0  # (ntp_time - system_time)
        self.last_sync_time: float = 0.0
        self.is_synced: bool = False
        self.sync_error: Optional[str] = None

    def sync(self, server: Optional[str] = None) -> bool:
        """
        Performs an SNTP query (RFC 4330) and updates the local clock offset.
        """
        servers = [server] if server else [self.server] + [s for s in self.DEFAULT_SERVERS if s != self.server]

        for srv in servers:
            try:
                client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                client.settimeout(self.timeout)
                # NTP request packet: LI=0, VN=3, Mode=3 (client) -> 0x1b, padded with 47 zero bytes
                msg = b"\x1b" + 47 * b"\0"
                t0 = time.time()
                client.sendto(msg, (srv, 123))
                data, _ = client.recvfrom(1024)
                t3 = time.time()
                client.close()

                if len(data) >= 48:
                    # Unpack 12 unsigned 32-bit ints; transmit timestamp is index 10 (seconds) and 11 (fraction)
                    unpacked = struct.unpack("!12I", data[:48])
                    ntp_sec = unpacked[10] - self.NTP_DELTA
                    ntp_frac = unpacked[11] / (2**32)
                    t_transmit = ntp_sec + ntp_frac

                    # Offset = ((t_receive - t0) + (t_transmit - t3)) / 2 ~ t_transmit - ((t0 + t3) / 2)
                    round_trip = t3 - t0
                    self.offset = t_transmit - (t0 + round_trip / 2.0)
                    self.last_sync_time = time.time()
                    self.is_synced = True
                    self.sync_error = None
                    self.server = srv
                    return True
            except Exception as e:
                self.sync_error = str(e)
                continue

        # If all NTP servers failed
        self.is_synced = False
        return False

    def get_time(self) -> float:
        """Returns current unix timestamp adjusted by NTP offset if synchronized."""
        # Auto-resync every 10 minutes if previously synced
        if self.is_synced and (time.time() - self.last_sync_time > 600):
            try:
                self.sync()
            except Exception:
                pass
        return time.time() + self.offset

    def get_datetime(self, tz_str: str = "UTC") -> datetime.datetime:
        """Returns timezone-aware datetime using NTP-synchronized timestamp."""
        ts = self.get_time()
        try:
            tz = ZoneInfo(tz_str)
        except Exception:
            tz = datetime.timezone.utc
        return datetime.datetime.fromtimestamp(ts, tz=tz)

    def get_status(self) -> Dict:
        """Returns current NTP synchronization status dictionary."""
        return {
            "synced": self.is_synced,
            "server": self.server,
            "offset_ms": round(self.offset * 1000.0, 2),
            "last_sync_age_s": round(time.time() - self.last_sync_time, 1) if self.last_sync_time else None,
            "error": self.sync_error
        }

    def format_timestamp(self, time_format: str = "zulu", tz_str: str = "UTC") -> str:
        """
        Formats timestamp string according to choice:
        'zulu'  -> '2026-10-14 23:30:15 UTC (Z)'
        'local' -> '2026-10-14 19:30:15 EDT'
        """
        if time_format.lower() in ("zulu", "utc", "z"):
            dt = self.get_datetime("UTC")
            sync_tag = " [NTP]" if self.is_synced else ""
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC") + " (Z)" + sync_tag
        else:
            dt = self.get_datetime(tz_str)
            sync_tag = " [NTP]" if self.is_synced else ""
            tz_abbr = dt.strftime("%Z") or tz_str
            return dt.strftime("%Y-%m-%d %H:%M:%S") + f" {tz_abbr}" + sync_tag
