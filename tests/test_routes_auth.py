import pytest
from httpx import AsyncClient, ASGITransport
import sys
import os
from unittest.mock import patch, MagicMock

# Add backend to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from main import app

@pytest.mark.asyncio
async def test_auth_check_setup_flow():
    # Mock supabase response for check-setup
    with patch('routes.auth.get_supabase') as mock_get_supabase:
        mock_supabase = MagicMock()
        mock_get_supabase.return_value = mock_supabase

        mock_supabase.table().select().limit().execute.return_value = MagicMock(data=[]) # No admin exists
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/api/auth/check-setup")
            assert response.status_code == 200
            assert response.status_code == 200
            assert response.json()["needs_setup"] is True


@pytest.mark.asyncio
async def test_auth_login_invalid_credentials():
    with patch('routes.auth.get_supabase') as mock_get_supabase:
        mock_supabase = MagicMock()
        mock_get_supabase.return_value = mock_supabase

        mock_supabase.table().select().eq().execute.return_value = MagicMock(data=[]) # User not found
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/api/auth/login", json={"email": "wrong@test.com", "password": "any"})
            assert response.status_code == 401
            assert "User not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_auth_user_management_protected():
    # Ensure admin-only routes are protected
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/auth/admin/users")
        assert response.status_code == 401 # Should fail without token
