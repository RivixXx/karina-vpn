import pytest

from src.models import is_mobile_email, mobile_email_for, primary_email_from_mobile


def test_mobile_naming():
    assert mobile_email_for("Mikhail") == "Mikhail__mobile"
    assert is_mobile_email("Mikhail__mobile")
    assert primary_email_from_mobile("Mikhail__mobile") == "Mikhail"


def test_nested_mobile_and_length_rejected():
    with pytest.raises(ValueError):
        mobile_email_for("Mikhail__mobile")
    assert len(mobile_email_for("x" * 56)) == 64
    with pytest.raises(ValueError):
        mobile_email_for("x" * 57)
