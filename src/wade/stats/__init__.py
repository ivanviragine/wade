"""Local usage statistics — SQLite store, pricing, aggregation and insights.

Layering: this package sits beside the git/provider layers. It may import
``wade.models``, ``wade.logging`` and leaf ``wade.utils`` modules, never
``wade.services`` / ``wade.cli``. Services own *when* to record; this package
owns *how* data is persisted and summarized.
"""
