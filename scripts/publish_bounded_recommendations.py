from __future__ import annotations

import json

from app.config import get_settings
from app.products.recommendations import MAX_RECOMMENDATION_FEATURES
from app.products.registry import ProductFamily, refresh_product_family
from app.repositories.database import (
    create_scheduler_database_engine,
    create_session_factory,
)


def main() -> int:
    engine = create_scheduler_database_engine(get_settings())
    try:
        result = refresh_product_family(
            ProductFamily.RECOMMENDATION,
            create_session_factory(engine),
        )
    finally:
        engine.dispose()
    if result.published_count != 1 or not 1 <= result.place_count <= MAX_RECOMMENDATION_FEATURES:
        raise RuntimeError("Bounded recommendation product was not published")
    print(
        json.dumps(
            {
                "published_count": result.published_count,
                "place_count": result.place_count,
                "maximum_place_count": MAX_RECOMMENDATION_FEATURES,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
