from typing import Any
import numpy as np
import xml.etree.ElementTree as ET
from gensim.models import KeyedVectors
from thefuzz import fuzz
import gensim.downloader as api
from querysource.providers.sources import httpSource


vector_model = api.load("word2vec-google-news-300")

# vector_model = KeyedVectors.load_word2vec_format(
#     "GoogleNews-vectors-negative300.bin",
#     binary=True
# )


def phrase_vector(phrase, model):
    """
    Convert a phrase to a vector by averaging word embeddings
    for the words that exist in the model's vocabulary.
    """
    words = phrase.lower().split()
    valid_embeddings = []
    for w in words:
        if w in model:
            valid_embeddings.append(model[w])
    if not valid_embeddings:
        # if no words in the model, return a zero-vector or handle specially
        return np.zeros(model.vector_size)
    # average them
    return np.mean(valid_embeddings, axis=0)


class rssapp(httpSource):
    url: str = "https://rss.app/feeds/{bundle_id}.xml"
    content_type: str = 'application/xml'
    _keywords: dict = {}
    use_gesim: bool = True
    threshold: float = 0.60
    fuzzy_threshold: int = 60

    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:
        bundle_id = request.match_info.get('var')
        self._args['bundle_id'] = bundle_id
        self._db = request.app['database']
        self._conditions = {}

    async def get_bundle(self, **kwargs) -> Any:
        try:
            bundle_id = self._args['bundle_id']
            if bundle_id not in self._keywords:
                try:
                    async with await self._db.acquire() as conn:
                        result = await conn.fetch_one(
                            f"SELECT keywords FROM rssapp.bundles_keywords WHERE bundle_id = '{bundle_id}';"
                        )
                    # Vectorized the keywords found:
                    if self.use_gesim:
                        keyword_vectors = [(kw, phrase_vector(kw, vector_model)) for kw in result['keywords']]
                        self._keywords[bundle_id] = keyword_vectors
                    else:
                        self._keywords[bundle_id] = [kw.lower() for kw in result['keywords']]
                except Exception as err:
                    self.logger.exception(err)
                    raise
            keywords = self._keywords[bundle_id]
            _ = await self.aquery()
            # Iterate over the news in xml parser:
            root = self._parser
            channel = root.find("channel")
            for item in channel.findall('item'):
                title_node = item.find("title")
                desc_node = item.find("description")
                title = title_node.text if title_node is not None else ""
                desc = desc_node.text if desc_node is not None else ""
                # Combine title + description to simplify checking, and lower them
                combined_text = f"{title.lower()} {desc.lower()}"
                matched = False
                if self.use_gesim:
                    matched = self._search_gesim(combined_text, keywords)
                else:
                    matched = self._search_fuzzy(combined_text, keywords)
                if not matched:
                    channel.remove(item)
            # at the end, convert the etree (root) object to string:
            self._result = ET.tostring(
                root,
                encoding='utf-8',
                method='xml',
            ).decode('utf-8')
            return self._result
        except Exception as err:
            self.logger.exception(err)
            raise

    def _search_gesim(self, text, keywords):
        """
        Search for the best match between the text and the keywords
        """
        # Convert combined text to a vector:
        item_vector = phrase_vector(text, vector_model)
        # 3. Compare similarity with each keyword vector
        for kw, kv in keywords:
            # Cosine similarity
            dot = np.dot(item_vector, kv)
            norm = np.linalg.norm(item_vector) * np.linalg.norm(kv)
            similarity = dot / norm if norm else 0.0

            if similarity >= self.threshold:  # pick a threshold
                print(f"Semantic Match (sim={similarity:.2f}) with '{kw}'")
                print("--------------------------------------------------")
                # break if you want just the first match
                return True
        return False

    def _search_fuzzy(self, text, keywords):
        """
        Search for the best match between the text and the keywords
        """
        for kw in keywords:
            # Fuzzy matching
            similarity = fuzz.partial_ratio(text, kw)
            if similarity >= self.fuzzy_threshold:  # pick a threshold
                print(f"Fuzzy Match (sim={similarity:.2f}) with '{kw}'")
                print("--------------------------------------------------")
                # break if you want just the first match
                return True
        return False
