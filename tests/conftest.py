"""
Pytest conftest.py – runs before any test module is imported.

Sets dummy environment variables so that module-level checks in
routes/auth.py and auth.py don't raise RuntimeError during test
collection.
"""
import os

# Set BEFORE any application code is imported
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "fake-test-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-service-role-key")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
