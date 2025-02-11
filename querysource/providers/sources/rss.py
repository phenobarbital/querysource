from typing import Any, List
from .http import httpSource
from lxml import etree

class rss(httpSource):
    """
    RSS Feed Source
    Fetches and filters RSS feeds based on keywords per bundle
    """
    method: str = 'post'
    base_url: str = 'https://rss.app/feeds/'
    accept: str = 'application/xml'
    content_type: str = 'application/json'
    
    # Initialize class attributes
    bundle: dict = {}
    bundle_id: str = None
    keywords: List[str] = []

    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:
        """Initialize RSS source with bundle info"""
        # Initialize default values
        self.bundle = {}
        self.bundle_id = None
        self.keywords = []

        self.logger.debug(f"RSS Init - Definition: {definition}")
        self.logger.debug(f"RSS Init - Conditions: {conditions}")

        # Get bundle information from conditions first
        if conditions and 'params' in conditions:
            params = conditions['params']
            self.logger.debug(f"RSS Init - Params from conditions: {params}")
            
            if isinstance(params, dict) and 'bundle' in params:
                self.bundle = params['bundle']
                self.logger.debug(f"RSS Init - Bundle from conditions: {self.bundle}")
                if isinstance(self.bundle, dict):
                    self.bundle_id = self.bundle.get('bundle_id')
                    self.keywords = self.bundle.get('keywords', [])
                    self.logger.debug(f"RSS Init - Extracted bundle_id: {self.bundle_id}, keywords: {self.keywords}")

        # Call parent's init after setting our attributes
        super().__post_init__(
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )

        # Set parameters
        self._args = conditions.copy() if conditions else {}

    async def query(self, data: dict = None):
        """
        Implementation of the abstract query method.
        Fetches RSS feed and filters items based on keywords.
        """
        if not self.bundle_id:
            raise ValueError('RSS Feed: Missing Bundle ID')

        self.url = f"{self.base_url}{self.bundle_id}.xml"
        
        try:
            # Get XML content using parent's query method
            result = await super().query(data)
            
            if self.keywords and self._parser is not None:
                # Get channel element
                channel = self._parser.find('.//channel')
                if channel is not None:
                    # Iterate through items and filter
                    for item in channel.findall('item'):
                        title = item.findtext('title', '').lower()
                        description = item.findtext('description', '').lower()
                        
                        # Remove item if no keywords match
                        if not any(keyword.lower() in title or keyword.lower() in description 
                                 for keyword in self.keywords):
                            channel.remove(item)
            
            # Return filtered XML as string if requested
            if getattr(self, 'return_string', False):
                return etree.tostring(
                    self._parser,
                    pretty_print=True,
                    xml_declaration=True,
                    encoding='UTF-8'
                )
            
            return self._parser

        except Exception as err:
            self.logger.exception(err)
            raise

    async def get_feed(self, bundle_id: str = None, bundle: dict = None):
        """
        Convenience method that wraps query()
        
        Args:
            bundle_id: Optional feed ID to override current one
            bundle: Optional bundle info containing bundle_id and keywords
        """
        if bundle:
            self.bundle = bundle
            self.bundle_id = bundle.get('bundle_id', self.bundle_id)
            self.keywords = bundle.get('keywords', self.keywords)
        elif bundle_id:
            self.bundle_id = bundle_id
            
        return await self.query() 