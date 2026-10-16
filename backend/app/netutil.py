import ipaddress

from fastapi import Request

from .config import get_settings


def _trusted() -> list:
    out = []
    for item in get_settings().trusted_proxies.split(","):
        if item.strip():
            out.append(ipaddress.ip_network(item.strip(), strict=False))
    return out


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    nets = _trusted()

    def trusted(ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in n for n in nets)

    if not nets or not trusted(peer):
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    for hop in reversed(hops):
        if not trusted(hop):
            return hop
    return peer
