# pyright: strict

from .config import (
    COMPUTER_IP,
    LOCAL_ROUTER,
    ROUTER_COUNT,
    online_hosts,
    random_source,
)


def get_host_ips() -> tuple[str, ...]:
    return tuple(
        sorted({address.rsplit(":", maxsplit=1)[0] for address in online_hosts})
    )


def random_router_parents(
    root: str,
    routers: tuple[str, ...],
) -> dict[str, str]:
    connected = [root]
    pending = [router for router in routers if router != root]
    random_source.shuffle(pending)
    parents: dict[str, str] = {}

    # Each router points toward an already-connected router.
    # Every chain therefore terminates at root without a loop.
    for router in pending:
        parents[router] = random_source.choice(connected)
        connected.append(router)

    return parents


def build_first_hops(
    routers: tuple[str, ...],
    endpoint_gateways: dict[str, str],
) -> dict[tuple[str, str], str]:
    router_set = frozenset(routers)
    endpoints = tuple(endpoint_gateways)
    devices = routers + endpoints
    first_hops: dict[tuple[str, str], str] = {}

    for destination in devices:
        root = (
            destination
            if destination in router_set
            else endpoint_gateways[destination]
        )
        router_parents = random_router_parents(root, routers)

        for source in routers:
            if source == destination:
                first_hops[(source, destination)] = source
            elif source == root:
                first_hops[(source, destination)] = destination
            else:
                first_hops[(source, destination)] = router_parents[source]

        # Endpoints never forward traffic. They send through one gateway.
        for source, gateway in endpoint_gateways.items():
            first_hops[(source, destination)] = (
                source if source == destination else gateway
            )

    return first_hops


if ROUTER_COUNT < 1:
    raise ValueError("ROUTER_COUNT must be positive")

router_ips = tuple(
    f"10.0.1.{number}" for number in range(1, ROUTER_COUNT + 1)
)
router_set = frozenset(router_ips)
host_ips = get_host_ips()
endpoint_ips = (COMPUTER_IP, *host_ips)
device_ips = frozenset((*router_ips, *endpoint_ips))

if len(device_ips) != len(router_ips) + len(endpoint_ips):
    raise ValueError("Device IPs must be unique")

remote_gateways = router_ips[1:] or router_ips
endpoint_gateways = {
    COMPUTER_IP: LOCAL_ROUTER,
    **{
        host_ip: random_source.choice(remote_gateways)
        for host_ip in host_ips
    },
}
first_hops = build_first_hops(router_ips, endpoint_gateways)


def trace_packet(source_ip: str, destination_ip: str, ttl: int) -> str:
    if ttl < 1:
        return "TTL must be at least 1."

    if source_ip not in device_ips:
        return f"UNKNOWN SOURCE {source_ip}"

    if destination_ip not in device_ips:
        return f"DESTINATION UNREACHABLE {destination_ip}"

    current = source_ip
    remaining_ttl = ttl

    while current != destination_ip:
        current = first_hops[(current, destination_ip)]

        if current == destination_ip:
            break

        if current in router_set:
            remaining_ttl -= 1

            if remaining_ttl == 0:
                return f"TTL EXPIRED AT {current}"

    return f"REACHED {destination_ip}"
