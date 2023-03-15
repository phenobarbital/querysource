from .abstract import BaseProvider


class dummyProvider(BaseProvider):
    """Example class for creating Data Providers for QS.
    """

    async def prepare_connection(self):
        """Signal run before connection is made.
        """
        await super(dummyProvider, self).prepare_connection()
        print('Running PREPARE')

    async def columns(self):
        """Return the columns (fields) involved on the query (when possible).
        """

    async def dry_run(self):
        """Running Build Query and return the Query to be executed (without execution).
        """
        print('RUNNING DRY-RUN')
        return ([], None)

    async def query(self):
        """Run a query on the Data Provider.
        """
        print('RUNNING QUERY')
        return ([], None)

    async def close(self):
        """Closing all resources used by the Provider.
        """
        print('RUNNING CLOSE')
