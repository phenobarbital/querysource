from typing import Any
import re
import numpy as np
import nltk
import xml.etree.ElementTree as ET
from thefuzz import fuzz
from groq import Groq
from querysource.providers.sources import httpSource
try:
    from gensim.models import KeyedVectors
except ImportError:
    KeyedVectors = None

nltk.download('punkt_tab')
nltk.download('averaged_perceptron_tagger_eng')

if KeyedVectors:
    vector_model = KeyedVectors.load_word2vec_format(
        "GoogleNews-vectors-negative300.bin",
        binary=True
    )


def split_keyword(keyword):
    tokens = nltk.word_tokenize(keyword)
    tagged = nltk.pos_tag(tokens)
    chunk_gram = r"""
        NP: {<DT>?<JJ>*<NN.*|CD>+}
        VP: {<VB.*><NP|PP|CLAUSE>+$}  # Define a grammar for verb phrases
        PP: {<IN><NP>}               # Define a grammar for prepositional phrases
        CLAUSE: {<NP><VP>}           # Define a grammar for clauses
    """
    chunk_parser = nltk.RegexpParser(chunk_gram)
    tree = chunk_parser.parse(tagged)
    phrases = []
    prev_subtree_label = None  # Keep track of the previous subtree label
    for subtree in tree.subtrees():
        if subtree.label() == 'NP' and subtree.label() != prev_subtree_label:
            phrase = " ".join([leaf[0] for leaf in subtree.leaves()])
            phrases.append(phrase)
        elif subtree.label() == 'PP':
            for child in subtree:
                if isinstance(child, nltk.Tree) and child.label() == 'NP' and child.label() != prev_subtree_label:
                    phrase = " ".join([leaf[0] for leaf in child.leaves()])
                    phrases.append(phrase)
        prev_subtree_label = subtree.label()  # Update the previous subtree label
    return phrases


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
    _negative_keywords: dict = {}
    use_gesim: bool = False
    threshold: float = 0.60
    fuzzy_threshold: int = 60
    llm_model: str = None
    use_llm: bool = False
    
    namespaces: dict = {
        'media': 'http://search.yahoo.com/mrss/',
        'dc': 'http://purl.org/dc/elements/1.1/',
        'content': 'http://purl.org/rss/1.0/modules/content/',
        'atom': 'http://www.w3.org/2005/Atom',
    }

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
        self.groq_client = Groq(api_key=self._env.get('GROQ_API_KEY'))
        self.llm_model = self._env.get('RSSAPP_LLM_MODEL', fallback='llama-3.3-70b-versatile')
        self.use_llm = self._env.getboolean('RSSAPP_USE_LLM', fallback=False)
        
        try:
            self._model = request.app['vector_models']["word2vec-google-news-300"]
            self.use_gesim = True
        except KeyError:
            self.logger.warning(
                "No vector model found for 'word2vec-google-news-300'"
            )
            self._model = None
            self.use_gesim = False
        self._conditions = {}
        self._negative_keywords = {}

    async def load_keywords(self, bundle_id: str, vector_model: Any):
        try:
            async with await self._db.acquire() as conn:
                result = await conn.fetch_one(
                    "SELECT keywords, negative_keywords, vector FROM rssapp.bundles_keywords WHERE bundle_id = $1;",
                    bundle_id
                )
                if not result:
                    # Handle case where no row is found
                    self.logger.warning(f"No row found for bundle_id={bundle_id}")
                    return None
            if self.use_gesim:
                # vectors and keywords:
                db_keywords = result['keywords']       # This should be a list of strings
                db_vector = result['vector']         # This is either None or JSON
                if db_vector is not None:
                    # Load the vector from the database
                    stored_vectors = self._encoder.loads(db_vector)
                    # Pair them up with keywords so you have (kw, vector) tuples
                    keyword_vectors = []
                    for kw, vec in zip(db_keywords, stored_vectors):
                        keyword_vectors.append((kw, vec))
                    self._keywords[bundle_id] = keyword_vectors
                else:
                    # Vectorized the keywords found:
                    keyword_vectors = [(kw, phrase_vector(kw, vector_model)) for kw in db_keywords]
                    self._keywords[bundle_id] = keyword_vectors
                    # Build a JSON-serializable structure (just list of lists):
                    vectors_to_store = []
                    for (kw, vector_array) in keyword_vectors:
                        # If 'vector_array' is a NumPy array, convert to list
                        if hasattr(vector_array, "tolist"):
                            vector_array = vector_array.tolist()
                        vectors_to_store.append(vector_array)
                    # Encode as JSON using your custom encoder
                    vector_json = self._encoder.dumps(vectors_to_store)
                    # Update the DB
                    update_sql = """
                    UPDATE rssapp.bundles_keywords
                    SET vector = $1
                    WHERE bundle_id = $2
                    """
                    await conn.execute(update_sql, vector_json, bundle_id)
            else:
                if result['negative_keywords']:
                    self._negative_keywords[bundle_id] = [kw.lower() for kw in result.get('negative_keywords', [])]
                self._keywords[bundle_id] = [kw.lower() for kw in result['keywords']]
            return True
        except Exception as err:
            self.logger.exception(err)
            return None

    async def get_bundle(self, **kwargs) -> Any:
        try:
            bundle_id = self._args['bundle_id']
            if bundle_id not in self._keywords:
                await self.load_keywords(bundle_id, self._model)

            keywords = self._keywords[bundle_id]
            negative_keywords = self._negative_keywords.get(bundle_id, [])
            _ = await self.aquery(namespaces=self.namespaces)
            # Iterate over the news items in the xml parser:
            root = self._parser
            channel = root.find("channel")
            for item in channel.findall('item'):
                title_node = item.find("title")
                desc_node = item.find("description")
                title = title_node.text if title_node is not None else ""
                desc = desc_node.text if desc_node is not None else ""
                # Combine title and description for easier matching:
                combined_text = f"{title.lower()} {desc.lower()}"

                if self.use_llm:
                    # LLM Search
                    matched = self._search_llm(combined_text, keywords, negative_keywords)
                    # Remove if not found in keywords
                    if not matched:
                        channel.remove(item)
                else:
                    if self.use_gesim:
                        matched = self._search_gesim(combined_text, keywords)
                        negative_matched = self._search_gesim(combined_text, negative_keywords)
                    else:
                        matched = self._search_fuzzy(combined_text, keywords)
                        negative_matched = self._search_fuzzy(combined_text, negative_keywords)
                    # New: allow regex matching as an alternative search strategy
                    if not matched:
                        matched = self._search_regex(combined_text, keywords)
                        negative_matched = self._search_regex(combined_text, negative_keywords)
                    # Remove if not found in keywords
                    if not matched:
                        channel.remove(item)
                    # Remove if found in negative keywords
                    if negative_matched and matched:
                        channel.remove(item)

            # Convert the updated XML tree back to a string:
            self._result = ET.tostring(
                root,
                encoding='utf-8',
                method='xml'
            ).decode('utf-8')
            return self._result
        except Exception as err:
            self.logger.exception(err)
            raise

    def _search_gesim(self, text, keywords):
        """
        Search for the best match between the text and the keywords using semantic similarity.
        """
        # Convert combined text to a vector:
        item_vector = phrase_vector(text, self._model)
        # Compare similarity with each keyword vector
        for kw, kv in keywords:
            # Cosine similarity
            dot = np.dot(item_vector, kv)
            norm = np.linalg.norm(item_vector) * np.linalg.norm(kv)
            similarity = dot / norm if norm else 0.0

            if similarity >= self.threshold:  # pick a threshold
                print(f"Semantic Match (sim={similarity:.2f}) with '{kw}'")
                print("--------------------------------------------------")
                return True
        return False

    def _search_fuzzy(self, text, keywords):
        """
        Search for the best match between the text and the keywords using fuzzy matching.
        """
        for kw in keywords:
            # split into phrases:
            similarity = 0
            sentences = split_keyword(kw if isinstance(kw, str) else kw[0])
            similarity = sum(fuzz.partial_ratio(text, sentence) for sentence in sentences)
            similarity /= len(sentences)
            if similarity >= self.fuzzy_threshold:  # pick a threshold
                print(f"Fuzzy Match (sim={similarity:.2f}) with '{kw if isinstance(kw, str) else kw[0]}'")
                print("--------------------------------------------------")
                return True
        return False

    def _search_regex(self, text, keywords):
        """
        Search for a regex match between the text and each keyword.
        For each keyword, a regex pattern is created (using re.escape to handle special characters)
        and a case-insensitive search is performed.
        """
        for kw in keywords:
            # Determine the keyword string whether in tuple form or as a plain string
            keyword_str = kw if isinstance(kw, str) else kw[0]
            pattern = re.compile(re.escape(keyword_str), re.IGNORECASE)
            if pattern.search(text):
                print(f"Regex Match with '{keyword_str}'")
                print("--------------------------------------------------")
                return True
        return False

    def _search_llm(self, text, keywords, negative_keywords):
        """
        Search for a match between the text and each keyword. Using LLM model.
        """
        keywords = ', '.join(keywords)
        negative_keywords = ', '.join(negative_keywords)
        completion = self.groq_client.chat.completions.create(
            model=self.llm_model,
            messages=[
                {
                    "role": "system",
                    "content": "You're a news editor and your role is filtering news based on keywords, all keywords need to compared in lowercase and uppercase on content"
                },
                {
                    "role": "user",
                    "content": f"Check if RSS news contain someone of the keywords '{keywords}' and not contains anyone of the keywords '{negative_keywords}'\n\nContent: {text}\n\nIf the content of RSS matches with conditions just response with the word 'TRUE', else, response with the word 'FALSE'"
                }
            ],
            temperature=1,
            max_completion_tokens=1024,
            top_p=1,
            stop=None,
        )
        response = completion.choices[0].message.content
        print('>>', response, True if response == 'TRUE' else False)
        return True if response == 'TRUE' else False