import asyncio
import unittest
import json
import numpy as np
import xml.etree.ElementTree as ET
from typing import Any

class DummyEncoder:
    @staticmethod
    def loads(s):
        return json.loads(s)

    @staticmethod
    def dumps(obj):
        return json.dumps(obj)

class DummyConnection:
    def __init__(self, result):
        self.result = result

    async def fetch_one(self, query, bundle_id):
        return self.result.get(query)

    async def execute(self, query, vector_json, bundle_id):
        self.last_update = (query, vector_json, bundle_id)
        return True

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def __aenter__(self):
        return self

class DummyDB:
    def __init__(self, result_map):
        self.result_map = result_map

    async def acquire(self):
        return DummyConnection(self.result_map)

class DummyVectorModel:
    def __init__(self):
        self.vector_size = 3

    def __contains__(self, key):
        return key.isalpha()

    def __getitem__(self, key):
        vec = np.array([len(key)] * self.vector_size, dtype=float)
        return vec

class DummyRequest:
    def __init__(self, bundle_id, db, vector_model):
        self.match_info = {'var': bundle_id}
        self.app = {
            'database': db,
            'vector_models': {"word2vec-google-news-300": vector_model}
        }

def dummy_aquery(self, namespaces):
    xml_str = """
    <rss>
      <channel>
        <item>
          <title>Good News</title>
          <description>This is really good news!</description>
        </item>
        <item>
          <title>Bad Report</title>
          <description>This is a bad report that should be filtered out.</description>
        </item>
        <item>
          <title>Neutral Update</title>
          <description>This update is neutral and does not match keywords.</description>
        </item>
      </channel>
    </rss>
    """
    self._parser = ET.ElementTree(ET.fromstring(xml_str)).getroot()
    return asyncio.sleep(0)

class TestableRssApp(rssapp):
    def __post_init__(self, definition: dict = None, conditions: dict = None, request: Any = None, **kwargs) -> None:
        super().__post_init__(definition, conditions, request, **kwargs)
        self._encoder = DummyEncoder()
        self._negative_keywords = {}

    async def aquery(self, namespaces):
        await dummy_aquery(self, namespaces)

class TestNegativeKeywordsBehavior(unittest.IsolatedAsyncioTestCase):
    async def test_negative_keywords_behavior(self):
        bundle_id = "test_bundle_neg"
        keywords_result = {
            "SELECT keywords, vector FROM rssapp.bundles_keywords WHERE bundle_id = $1;":
                {"keywords": ["anything"], "vector": None}
        }
        negative_result = {
            "SELECT negative_keywords FROM rssapp.bundles_keywords WHERE bundle_id = $1;":
                {"negative_keywords": ["bad"]}
        }
        result_map = {**keywords_result, **negative_result}
        dummy_db = DummyDB(result_map)
        dummy_vector_model = DummyVectorModel()
        request = DummyRequest(bundle_id, dummy_db, dummy_vector_model)

        def custom_dummy_aquery(self, namespaces):
            xml_str = """
            <rss>
              <channel>
                <item>
                  <title>Safe Update</title>
                  <description>This update is safe.</description>
                </item>
                <item>
                  <title>Bad Update</title>
                  <description>This update is bad and should be removed.</description>
                </item>
              </channel>
            </rss>
            """
            self._parser = ET.ElementTree(ET.fromstring(xml_str)).getroot()
            return asyncio.sleep(0)

        app_instance = TestableRssApp(definition=None, conditions=None, request=request)
        await app_instance.load_keywords(bundle_id, dummy_vector_model)
        await app_instance.load_negative_keywords(bundle_id)
        app_instance.aquery = custom_dummy_aquery.__get__(app_instance)
        result_xml = await app_instance.get_bundle()
        root = ET.fromstring(result_xml)
        channel = root.find("channel")
        items = channel.findall("item")
        self.assertEqual(len(items), 1)
        remaining_title = items[0].find("title").text
        self.assertIn("Safe Update", remaining_title)

if __name__ == '__main__':
    unittest.main()
