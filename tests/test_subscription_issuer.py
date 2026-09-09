from pathlib import Path

import pytest

from src.integrations.happ import SubscriptionIssuerError, issue_subscription


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body.encode("utf-8")


def opener_with(body):
    return lambda request, timeout: Response(body)


def write_qr(value, path):
    path.write_text("QR:" + value, encoding="utf-8")


def test_success_creates_expected_files_and_returns_public_url(tmp_path):
    result = issue_subscription(
        "https://sub.example.test/safe_id123",
        output_dir=tmp_path,
        public_base="https://vpn.example.test/connect/",
        opener=opener_with('<a href="happ://crypt5/synthetic-value">open</a>'),
        qr_writer=write_qr,
    )
    assert result == "https://vpn.example.test/connect/safe_id123.html"
    assert (tmp_path / "safe_id123.crypt5").read_text(encoding="utf-8") == "happ://crypt5/synthetic-value"
    assert (tmp_path / "safe_id123.png").read_text(encoding="utf-8").startswith("QR:happ://")
    page = (tmp_path / "safe_id123.html").read_text(encoding="utf-8")
    assert "safe_id123.png" in page and "happ://crypt5/synthetic-value" in page


def test_missing_crypt5_writes_nothing(tmp_path):
    with pytest.raises(SubscriptionIssuerError, match="Crypt5"):
        issue_subscription(
            "https://sub.example.test/safe_id123", output_dir=tmp_path,
            public_base="https://vpn.example.test/connect", opener=opener_with("no link"),
            qr_writer=write_qr,
        )
    assert list(tmp_path.iterdir()) == []


def test_transport_error_preserves_cause(tmp_path):
    def fail(*args, **kwargs):
        raise OSError("synthetic transport")

    with pytest.raises(SubscriptionIssuerError) as caught:
        issue_subscription(
            "https://sub.example.test/safe_id123", output_dir=tmp_path,
            public_base="https://vpn.example.test/connect", opener=fail,
        )
    assert isinstance(caught.value.__cause__, OSError)
    assert "synthetic transport" not in str(caught.value)


@pytest.mark.parametrize("url", [
    "https://sub.example.test/../escape", "https://sub.example.test/a%2Fb",
    "https://sub.example.test/short", "https://sub.example.test/" + "x" * 129,
])
def test_invalid_subid_cannot_escape_output(tmp_path, url):
    with pytest.raises(SubscriptionIssuerError):
        issue_subscription(
            url, output_dir=tmp_path, public_base="https://vpn.example.test/connect",
            opener=opener_with('<a href="happ://crypt5/value">open</a>'), qr_writer=write_qr,
        )
    assert list(tmp_path.iterdir()) == []


def test_request_contains_subscription_url(tmp_path):
    captured = {}

    def opener(request, timeout):
        captured["data"] = request.data
        captured["timeout"] = timeout
        return Response('<a href="happ://crypt5/value">open</a>')

    issue_subscription(
        "https://sub.example.test/safe_id123", output_dir=tmp_path,
        public_base="https://vpn.example.test/connect", opener=opener, qr_writer=write_qr,
    )
    assert b"https%3A%2F%2Fsub.example.test%2Fsafe_id123" in captured["data"]
    assert captured["timeout"] == 20
