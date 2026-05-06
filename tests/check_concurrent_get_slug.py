"""Live stress probe for the QueryModel.Meta.connection race fix.

Spawns N concurrent ``Connection().get_slug()`` calls against the
configured Postgres and reports a Counter of outcomes. Before the
``_connection=`` migration this would intermittently produce
``Missing Connection for Model: <class …QueryModel>`` under load.

Usage
-----
::

    source .venv/bin/activate
    python lab/check_concurrent_get_slug.py [SLUG] [CONCURRENCY] [ITERATIONS]

Defaults: SLUG=__nonexistent_slug_for_race_test__, CONCURRENCY=50,
ITERATIONS=20. The slug doesn't need to exist — ``SlugNotFound`` proves
the connection plumbing succeeded; only ``MISSING_CONNECTION`` indicates
the bug returned. Exits non-zero if any race symptom is observed.
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter

from querysource.exceptions import QueryException, SlugNotFound
from querysource.interfaces.connections import Connection


async def one_call(conn: Connection, slug: str) -> str:
    try:
        await conn.get_slug(slug)
        return "ok"
    except SlugNotFound:
        # Plumbing worked; the slug just isn't in the DB. Fine.
        return "slug_not_found"
    except QueryException as ex:
        msg = str(ex)
        if "Missing Connection" in msg:
            return "MISSING_CONNECTION"
        return f"query_error: {msg[:80]}"
    except Exception as ex:  # pylint: disable=broad-except
        return f"other_error: {type(ex).__name__}: {str(ex)[:80]}"


async def run(slug: str, concurrency: int, iterations: int) -> int:
    conn = Connection()
    summary: Counter[str] = Counter()
    for i in range(iterations):
        results = await asyncio.gather(
            *[one_call(conn, slug) for _ in range(concurrency)]
        )
        for r in results:
            summary[r] += 1
        print(f"  iteration {i + 1}/{iterations} done")

    print()
    print("=== SUMMARY ===")
    for k, v in summary.most_common():
        marker = "  <-- RACE REGRESSION!" if k == "MISSING_CONNECTION" else ""
        print(f"  {v:6d}  {k}{marker}")
    return summary["MISSING_CONNECTION"]


def main() -> int:
    slug = sys.argv[1] if len(sys.argv) > 1 else "__nonexistent_slug_for_race_test__"
    concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    iterations = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    print(
        f"Running slug={slug!r}, concurrency={concurrency}, "
        f"iterations={iterations} ({concurrency * iterations} total calls)"
    )
    misses = asyncio.run(run(slug, concurrency, iterations))
    return 0 if misses == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
