"""Global test configuration that prevents access to local development data."""

import os


os.environ.setdefault("HEARO_DATABASE_URL", "sqlite+pysqlite:///:memory:")
