"""Test configuration.

Tests must never touch the production database or the real bot token. This file
forces a throwaway SQLite database and a test bot token before the application
is imported. It runs before any test module, so the override cannot be skipped.
"""
import os
import tempfile

_tmp_dir = tempfile.mkdtemp(prefix="koreahub-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_dir}/test.db"
os.environ["BOT_TOKEN"] = "123456:TEST-TOKEN"
os.environ["ADMIN_IDS"] = "999"
os.environ["ADMIN_API_KEY"] = "test-admin-key"
os.environ["NOTIFICATIONS_ENABLED"] = "false"
