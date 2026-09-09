from dataclasses import dataclass


@dataclass(frozen=True)
class ClientInfo:
    email: str
    status: str
    enabled: bool
    expiry_time_ms: int
    expiry_text: str
    device_count: int
    device_limit: int
    total_traffic_bytes: int
    used_traffic_bytes: int
    inbound_ids: tuple[int, ...]
    sub_id: str
    connect_url: str


@dataclass(frozen=True)
class DeviceInfo:
    id: int
    model: str
    os_name: str
    os_version: str
    user_agent: str
    first_seen_ms: int
    last_seen_ms: int


@dataclass(frozen=True)
class TrafficInfo:
    used_bytes: int
    limit_bytes: int
    remaining_bytes: int | None
    percent_used: float | None


@dataclass(frozen=True)
class CreateClientResult:
    client: ClientInfo
    subscription_page: str | None
    issue_warning: str | None


@dataclass(frozen=True)
class DeleteClientResult:
    email: str
    removed: bool
    file_cleanup_warning: str | None


@dataclass(frozen=True)
class ExpiringClient:
    email: str
    expiry_time_ms: int
    days_remaining: float
    enabled: bool = True
    expiry_text: str = ""
