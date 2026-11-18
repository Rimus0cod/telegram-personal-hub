"""Offline catalog source adapters.

Each module converts one official dataset into the reviewed JSON shape consumed
by ``tools.catalog_import``.  Adapters never run inside the FastAPI process, so
Nutrition has no runtime dependency on an external provider.
"""

from . import usda  # noqa: F401
