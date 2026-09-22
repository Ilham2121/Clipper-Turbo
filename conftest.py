"""Isolate pytest from the production SQLite DB (Fase 2 Iterasi 2.3).

Sets CLIPPER_DB_PATH before test modules import backend.models.db,
so the global `db` singleton points at a temp file instead of
storage/clipper.db. Prevents test runs from wiping real job history.
"""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="clipper-test-")
os.environ["CLIPPER_DB_PATH"] = os.path.join(_tmp, "test-clipper.db")
