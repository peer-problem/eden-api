from __future__ import annotations

from sqlalchemy import Connection, text


class MariaDBAdvisoryLock:
    def __init__(self, connection: Connection, name: str, timeout_seconds: int = 0) -> None:
        self.connection = connection
        self.name = name[:64]
        self.timeout_seconds = timeout_seconds
        self.acquired = False

    def __enter__(self) -> MariaDBAdvisoryLock:
        acquired = self.connection.execute(
            text("SELECT GET_LOCK(:name, :timeout)"),
            {"name": self.name, "timeout": self.timeout_seconds},
        ).scalar_one()
        self.acquired = acquired == 1
        return self

    def __exit__(self, *_: object) -> None:
        if self.acquired:
            self.connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": self.name})
            self.acquired = False
