"""
Test.
"""
# -*- coding: utf-8 -*-
#!/usr/bin/env python3
import asyncio
from datetime import datetime
from asyncdb.utils import cPrint
from querysource.queries import QS


async def use_query(slug, conditions):
    try:
        startTime = datetime.now()
        qry = QS(
            slug=slug,
            conditions=conditions,
        )
        async with qry:
            result, error = await qry.query(
                output_format="pandas"
            )
            if error:
                cPrint(error, "ERROR")
            cPrint(f'RESULT: {len(result)}')
            print(type(result))
            print(result)
            print(
                "Generated in: %s" % (datetime.now() - startTime)
            )
    finally:
        pass

slugs = [
    # 'roadshows_capacity_stores_stores_16-01-26'
    "roadshows_capacity_stores_stores"
]

conditions = {
    "retailer":["AT&T"],
    "filter":{"state_name":["California"]}
}


def main():
    try:
        for slug in slugs:
            asyncio.run(use_query(slug, conditions))
    finally:
        print('Closing All Connections ...')


if __name__ == '__main__':
    main()
