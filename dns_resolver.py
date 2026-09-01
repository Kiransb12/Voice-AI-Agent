"""Robust DNS Resolver Fallback.

If the local Windows system / ISP DNS resolver times out or fails (errno 11001),
this module automatically falls back to querying public DNS (8.8.8.8 / 1.1.1.1)
directly over UDP, preventing "timed out during opening handshake" errors on Deepgram and Cartesia.
"""

import socket
import struct
from loguru import logger

_orig_getaddrinfo = socket.getaddrinfo
_dns_cache = {}


def _query_public_dns(domain: str, server: str = "8.8.8.8") -> str | None:
    """Direct lightweight UDP DNS query to public DNS."""
    q_id = 0x1A2B
    flags = 0x0100  # Recursion desired
    header = struct.pack(">HHHHHH", q_id, flags, 1, 0, 0, 0)
    parts = domain.split(".")
    qname = b"".join(bytes([len(p)]) + p.encode() for p in parts) + b"\x00"
    packet = header + qname + struct.pack(">HH", 1, 1)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.5)
    try:
        sock.sendto(packet, (server, 53))
        data, _ = sock.recvfrom(1024)
        ancount = struct.unpack(">H", data[6:8])[0]
        if ancount > 0:
            return socket.inet_ntoa(data[-4:])
    except Exception:
        return None
    finally:
        sock.close()
    return None


def _robust_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    try:
        return _orig_getaddrinfo(host, port, family, type, proto, flags)
    except socket.gaierror:
        # Check in-memory cache first
        if host in _dns_cache:
            return _orig_getaddrinfo(_dns_cache[host], port, family, type, proto, flags)

        # Fallback to public DNS (8.8.8.8 then 1.1.1.1)
        for dns_server in ("8.8.8.8", "1.1.1.1"):
            ip = _query_public_dns(host, server=dns_server)
            if ip:
                _dns_cache[host] = ip
                logger.info(f"[DNS Fallback] Resolved {host} -> {ip} via {dns_server}")
                return _orig_getaddrinfo(ip, port, family, type, proto, flags)

        raise


def setup_dns_fallback():
    """Patches socket.getaddrinfo with automatic public DNS fallback."""
    socket.getaddrinfo = _robust_getaddrinfo
