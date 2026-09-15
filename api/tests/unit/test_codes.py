import pytest
from pydantic import BaseModel, ValidationError

from app.api.v1.schemas import CountryCode, CurrencyCode


class CodeModel(BaseModel):
    country: CountryCode
    currency: CurrencyCode


def test_iso_codes_are_normalized_to_uppercase() -> None:
    value = CodeModel(country="kr", currency="krw")

    assert value.country == "KR"
    assert value.currency == "KRW"


@pytest.mark.parametrize(
    ("country", "currency"),
    [
        ("ZZ", "KRW"),
        ("KR", "WON"),
        ("KOR", "KRW"),
        ("KR", "KR"),
    ],
)
def test_non_iso_codes_are_rejected(country: str, currency: str) -> None:
    with pytest.raises(ValidationError):
        CodeModel(country=country, currency=currency)
