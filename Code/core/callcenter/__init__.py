"""Call Center Analysis core: pipeline, AI service clients and data access.

Every external dependency (Blob Storage, Cosmos DB, Speech, Language, OpenAI,
Event Hubs) sits behind a small interface with two implementations:

* a local/mock implementation used in local mode (no Azure keys needed), and
* the real Azure implementation, selected through environment variables.

See ``callcenter.config`` for the switches.
"""

__version__ = "0.1.0"
