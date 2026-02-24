"""Local stub for `jito` SDK used in tests and dry-run environments.

This provides a minimal `jito.searcher_client.SearcherClient` implementation
that simulates bundle submission and status checks so the repository can run
without the full upstream SDK installed.

If you want real Jito behavior, install the upstream SDK and remove this
stub from your PYTHONPATH or uninstall it.
"""

__all__ = ["searcher_client"]
