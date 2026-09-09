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


MOBILE_SUFFIX = "__mobile"


def is_mobile_email(email: str) -> bool:
    return email.endswith(MOBILE_SUFFIX)


def primary_email_from_mobile(email: str) -> str:
    if not is_mobile_email(email) or is_mobile_email(email[:-len(MOBILE_SUFFIX)]):
        raise ValueError("invalid mobile credential")
    return email[:-len(MOBILE_SUFFIX)]


def mobile_email_for(primary_email: str) -> str:
    if is_mobile_email(primary_email):
        raise ValueError("nested mobile credential")
    result = primary_email + MOBILE_SUFFIX
    if len(result) > 64:
        raise ValueError("mobile credential exceeds 64 characters")
    return result


@dataclass(frozen=True)
class ClientBundle:
    primary: ClientInfo
    mobile: ClientInfo | None


@dataclass(frozen=True)
class CreateClientBundleResult:
    bundle: ClientBundle
    subscription_page: str | None


@dataclass(frozen=True)
class ExternalLinkInput:
    kind: str
    value: str
    remark: str


@dataclass(frozen=True)
class MobileMigrationPlan:
    primary_email: str
    mobile_email: str
    primary_exists: bool
    mobile_exists: bool
    primary_inbound_ids: tuple[int, ...]
    mobile_inbound_ids: tuple[int, ...]
    primary_expiry: int
    mobile_expiry: int | None
    primary_hwid_limit: int
    mobile_hwid_limit: int | None
    primary_total_bytes: int
    mobile_total_bytes: int | None
    mobile_subscription_url_present: bool
    mobile_subscription_url_correct: bool
    managed_external_link_count: int
    needs_mobile_create: bool
    needs_external_link_update: bool
    needs_primary_detach: bool
    needs_mobile_quota_fix: bool
    needs_expiry_sync: bool
    needs_hwid_sync: bool
    already_migrated: bool
    warnings: tuple[str, ...]
    blocking_errors: tuple[str, ...]
