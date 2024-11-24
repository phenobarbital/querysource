class QueryCache:
    """QueryCache class."""

    def __init__(self, backend, **kwargs):
        """QueryCache constructor."""
        self.backend = backend
        self.kwargs = kwargs

    def get(self, query):
        """Get method."""
        return self.backend.get(query)

    def set(self, query, result):
        """Set method."""
        return self.backend.set(query, result)

    def delete(self, query):
        """Delete method."""
        return self.backend.delete(query)

    def flush(self):
        """Flush method."""
        return self.backend.flush()
