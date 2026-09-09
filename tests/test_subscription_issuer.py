from pathlib import Path

import pytest

from src.integrations.subscription import SubscriptionIssuerError, issue_subscription


def write_qr(value, path):
    path.write_text("QR:" + value, encoding="utf-8")


def test_direct_https_subscription_is_written_and_verified(tmp_path):
    url = "https://sub.example.test/safe_id123"
    result = issue_subscription(
        url, output_dir=tmp_path,
        public_base="https://vpn.example.test/connect/", qr_writer=write_qr,
    )

    assert result == "https://vpn.example.test/connect/safe_id123.html"
    assert (tmp_path / "safe_id123.png").read_text(encoding="utf-8") == "QR:" + url
    page = (tmp_path / "safe_id123.html").read_text(encoding="utf-8")
    assert 'href="https://sub.example.test/safe_id123"' in page
    assert 'src="safe_id123.png"' in page
    assert "crypt5" not in page.lower()
    assert not list(tmp_path.glob("*.crypt5"))
    assert not list(tmp_path.glob("*.tmp"))


def test_qr_writer_failure_preserves_existing_artifacts(tmp_path):
    html_file = tmp_path / "safe_id123.html"
    png_file = tmp_path / "safe_id123.png"
    html_file.write_text("old html", encoding="utf-8")
    png_file.write_text("old png", encoding="utf-8")

    def fail(*_args):
        raise OSError("synthetic writer failure")

    with pytest.raises(SubscriptionIssuerError) as caught:
        issue_subscription(
            "https://sub.example.test/safe_id123", output_dir=tmp_path,
            public_base="https://vpn.example.test/connect", qr_writer=fail,
        )
    assert isinstance(caught.value.__cause__, OSError)
    assert html_file.read_text(encoding="utf-8") == "old html"
    assert png_file.read_text(encoding="utf-8") == "old png"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("url", [
    "http://sub.example.test/safe_id123",
    "https://user:pass@sub.example.test/safe_id123",
    "https://sub.example.test/../escape",
    "https://sub.example.test/a%2Fb",
    "https://sub.example.test/short",
    "https://sub.example.test/safe_id123?token=value",
    "https://sub.example.test/" + "x" * 129,
])
def test_invalid_subscription_writes_nothing(tmp_path, url):
    with pytest.raises(SubscriptionIssuerError):
        issue_subscription(
            url, output_dir=tmp_path, public_base="https://vpn.example.test/connect",
            qr_writer=write_qr,
        )
    assert list(tmp_path.iterdir()) == []
