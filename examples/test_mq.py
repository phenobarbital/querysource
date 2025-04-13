import asyncio
from querysource.queries.multi import MultiQS


async def test_multi_query():
    mq = MultiQS(
        queries={
            "hours": {
                "query": "SELECT store_id, sum(hours) as worked_hours, sum(hours) FILTER(WHERE pay_code in ('REGULAR', 'TRAINING')) as valid_hours,  min(in_time) as in_time, max(out_time) as out_time, count(distinct position_id) as num_employees FROM walmart.worked_hours WHERE pay_date between first_day('2023-01-01') and '2023-01-30' GROUP BY store_id"
            },
            "stores": {
                "slug": "walmart_stores",
                "fields": ["store_id", "store_name"]
            }
        }
    )
    result, options = await mq.execute()
    print('Options:', options)
    print('Result:', result)


if __name__ == "__main__":
    asyncio.run(test_multi_query())
# The above code is an example of how to run a multi-query using the QuerySource library.
