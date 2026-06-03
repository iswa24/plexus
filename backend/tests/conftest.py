"""Test setup: force demo mode and a throwaway DB before the app imports."""
import os
import tempfile

os.environ["PLEXUS_DEMO_MODE"] = "true"
_db = os.path.join(tempfile.gettempdir(), "plexus_test.db")
if os.path.exists(_db):
    os.remove(_db)
os.environ["PLEXUS_DB_PATH"] = _db
