from __future__ import annotations

import base64
import hashlib
import io
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.domain.enums import SourceStatus
from app.domain.ids import stable_eden_id
from app.sources.base import FetchResult, RawItem, SourceAdapter
from app.sources.http import SecureSourceClient

MOIS_CODE_URL = (
    "https://www.mois.go.kr/cmm/fms/FileDown.do?atchFileId=FILE_00147311ctH5-ah&fileSn=1"
)
MOIS_EFFECTIVE_DATE = "2026-07-20"


@dataclass(frozen=True, slots=True)
class ParsedArea:
    eden_area_id: str
    administrative_code: str
    name_ko: str
    parent_area_id: str | None
    level: str
    valid_from: str


class MoisAreaAdapter(SourceAdapter):
    source_id = "SRC_MOIS_ADMIN_CODES"

    def __init__(
        self,
        timeout_seconds: float,
        max_response_bytes: int,
        max_requests: int = 20,
        max_total_bytes: int = 8 * 1024 * 1024,
        max_run_seconds: float = 120.0,
    ) -> None:
        self.client = SecureSourceClient(
            {"www.mois.go.kr"},
            timeout_seconds,
            max_response_bytes,
            max_requests,
            max_total_bytes,
            max_run_seconds,
        )

    def fetch(self, scope: dict[str, Any]) -> FetchResult:
        del scope
        try:
            payload, content_type, final_url = self.client.get(MOIS_CODE_URL)
            parse_mois_archive(payload)
        except Exception as exc:
            return FetchResult(
                status=SourceStatus.DEGRADED,
                reason=f"행정코드 수집 또는 파싱 실패: {type(exc).__name__}",
            )
        return FetchResult(
            status=SourceStatus.AVAILABLE,
            data_as_of=datetime(2026, 7, 20, tzinfo=UTC),
            items=(
                RawItem(
                    external_key="jscode20260720.zip",
                    source_updated_at=datetime(2026, 7, 20, tzinfo=UTC),
                    observed_at=datetime(2026, 7, 20, tzinfo=UTC),
                    content_type=content_type or "application/zip",
                    body={
                        "source_url": final_url,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "base64": base64.b64encode(payload).decode("ascii"),
                    },
                ),
            ),
        )


def parse_mois_archive(payload: bytes) -> list[ParsedArea]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        candidates = [name for name in archive.namelist() if name.startswith("KIKcd_H.")]
        text_candidates = [name for name in candidates if not name.endswith(".xlsx")]
        if len(text_candidates) != 1:
            raise ValueError("MOIS archive must contain exactly one KIKcd_H text file")
        content = archive.read(text_candidates[0]).decode("cp949")
    areas: list[ParsedArea] = []
    for line in content.splitlines()[1:]:
        tokens = line.split()
        if len(tokens) < 3:
            continue
        code = tokens[0]
        if len(code) != 10 or not code.isdigit():
            continue
        if code.endswith("00000000"):
            level = "sido"
            name = tokens[1]
            parent_id = None
        elif code.endswith("00000"):
            level = "sigungu"
            name = tokens[-2]
            parent_code = f"{code[:2]}00000000"
            parent_id = stable_eden_id("area", "MOIS_ADMIN", parent_code)
        else:
            continue
        valid_from = datetime.strptime(tokens[-1], "%Y%m%d").date().isoformat()
        areas.append(
            ParsedArea(
                eden_area_id=stable_eden_id("area", "MOIS_ADMIN", code),
                administrative_code=code,
                name_ko=name,
                parent_area_id=parent_id,
                level=level,
                valid_from=valid_from,
            )
        )
    if len({area.administrative_code for area in areas}) != 296:
        raise ValueError("MOIS nationwide sido/sigungu completeness count changed")
    return areas
