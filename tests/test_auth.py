import pytest
from httpx import AsyncClient, ASGITransport
import sys
import os

# Ensure the app can find the routes natively
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from main import app

@pytest.mark.asyncio
async def test_auth_login_missing_fields():
    # Attempting to login without password
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/auth/login", json={"email": "test@test.com"})
        
        # FastAPI should automatically reject incomplete models with 422 Unprocessable Entity
        assert response.status_code == 422

@pytest.mark.asyncio
async def test_auth_get_me_unauthorized():
    # Attempting to access protected route without Bearer token
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/auth/me")
        
        # Custom dependency throws 401 if 'Authorization' header is missing
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid or missing token"
