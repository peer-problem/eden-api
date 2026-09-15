from app.ingestion.service import compress_raw_content
from app.repositories.models import RawRecord


def test_raw_json_and_text_payloads_round_trip_from_bounded_compression() -> None:
    json_body = {"value": "반복" * 1_000, "items": [1, 2, 3]}
    encoding, blob, uncompressed, compressed = compress_raw_content(json_body, None)
    json_record = RawRecord(
        body_json=None,
        body_text=None,
        body_encoding=encoding,
        body_blob=blob,
        uncompressed_bytes=uncompressed,
        compressed_bytes=compressed,
    )
    text_encoding, text_blob, text_uncompressed, text_compressed = compress_raw_content(
        None,
        "source text" * 1_000,
    )
    text_record = RawRecord(
        body_json=None,
        body_text=None,
        body_encoding=text_encoding,
        body_blob=text_blob,
        uncompressed_bytes=text_uncompressed,
        compressed_bytes=text_compressed,
    )

    assert json_record.decoded_body_json() == json_body
    assert json_record.decoded_body_text() is None
    assert text_record.decoded_body_text() == "source text" * 1_000
    assert text_record.decoded_body_json() is None
    assert compressed < uncompressed
    assert text_compressed < text_uncompressed


def test_raw_decoder_supports_legacy_rows_and_rejects_corrupt_metadata() -> None:
    legacy = RawRecord(body_json={"legacy": True}, body_text=None)
    encoding, blob, uncompressed, compressed = compress_raw_content({"value": 1}, None)
    corrupt = RawRecord(
        body_json=None,
        body_text=None,
        body_encoding=encoding,
        body_blob=blob,
        uncompressed_bytes=uncompressed,
        compressed_bytes=compressed + 1,
    )

    assert legacy.decoded_body_json() == {"legacy": True}
    try:
        corrupt.decoded_body_json()
    except ValueError as exc:
        assert "size does not match" in str(exc)
    else:
        raise AssertionError("corrupt raw payload metadata was accepted")
