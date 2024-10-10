import asyncio
from navconfig import BASE_DIR
from querysource.providers.sources.ga import ga


conditions = {
    "property_id": "283942027",
    "account_prefix": "POLESTAR_GA4",
    "project_id": "polestar-analytics-378119",
    "start_date": "2023-01-01",
    "end_date": "2023-03-30",
    "dimensions": ["userAgeBracket"],
    "metric": ["sessions", "totalUsers", "newUsers", "engagedSessions", "sessionsPerUser"]
}

async def test_ga():
    ga4 = ga(
        conditions=conditions
    )
    result = await ga4.report()
    print(result)

if __name__ == '__main__':
    asyncio.run(test_ga())
