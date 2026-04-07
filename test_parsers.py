import asyncio
from querysource.models import QueryModel
from querysource.parsers.mongo import MongoParser
from querysource.parsers.elastic import ElasticParser
from querysource.parsers.cql import CQLParser
from querysource.parsers.arangodb import ArangoDBParser
from querysource.parsers.rethink import RethinkParser

class DummyModel:
    def __init__(self):
        self.provider = 'dummy'
        self.source = 'troc_task_execution'
        self.query_slug = 'test_slug'
        self.query_raw = 'SELECT * FROM test'
        self.is_raw = False
        self.attributes = {}
        self.cond_definition = {}

    def __getattr__(self, name):
        return None

async def test_parsers():
    parsers = {
        'mongo': MongoParser,
        'elastic': ElasticParser,
        'cql': CQLParser,
        'arangodb': ArangoDBParser,
        'rethink': RethinkParser
    }

    # QueryModel initialized with minimal properties including a dummy query
    model = DummyModel()
    
    for name, parser_cls in parsers.items():
        print(f"\n--- Testing {name} ---")
        try:
            # Instantiate parser
            p = parser_cls(definition=model, conditions={})
            
            # Simulate basic parsing
            q = await p.build_query()
            print(f"Successfully generated query for {name}.")
            print(f"Output type: {type(q)}")
            
        except Exception as e:
            import traceback
            print(f"Error testing {name}:")
            traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(test_parsers())
