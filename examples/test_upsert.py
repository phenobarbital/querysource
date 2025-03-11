import asyncio
from datamodel import BaseModel, Field
from querysource.conf import async_default_dsn
from querysource.outputs.tables import PgOutput

class Client(BaseModel):
    client_id: int = Field(primary_key=True)
    client_name: str
    description: str
    status: bool = Field(default=True)
    orgid: int

    class Meta:
        strict = True
        table = 'clients'
        schema = 'networkninja'


async def main():
    output = PgOutput(dsn=async_default_dsn, use_async=True)
    # Create a Default Client:
    client = Client(
        client_id=1,
        client_name='Default Client',
        description='Default Client',
        orgid=1
    )
    async with output as conn:
        result = await conn.do_upsert(client)
        print(result)

if __name__ == '__main__':
    asyncio.run(main())
