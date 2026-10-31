"""Test configuration.

Tests must never touch the production database. This file forces a throwaway
SQLite database in a temporary directory before the application is imported.
It runs before any test module, so the override cannot be skipped.
"""
import os
import tempfile

_tmp_dir = tempfile.mkdtemp(prefix="koreahub-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_dir}/test.db"
