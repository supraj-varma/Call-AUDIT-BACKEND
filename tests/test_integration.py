import pytest
from httpx import AsyncClient, ASGITransport
import sys
import os

# Ensure the app can find the routes natively
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from main import app

@pytest.mark.asyncio
async def test_integration_endpoints():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        
        # 1. Test Status Endpoint Integration (Filesystem integration)
        res_status = await ac.get("/api/status/invalid_job_id_123")
        # Should cleanly return 404 because the job doesn't exist on disk
        assert res_status.status_code == 404
        assert res_status.json()["detail"] == "Job not found."
        
        # 2. Test Records List Integration (Database/Local Storage fallback integration)
        res_records = await ac.get("/api/records")
        # Should return a 200 OK and a valid JSON list (either from Supabase or Local records disk)
        assert res_status.status_code == 404 # Assert existing
        assert res_records.status_code == 200
        assert isinstance(res_records.json(), list)
        
        # 3. Test Cross-Origin Resource Sharing (CORS) Middleware Integration
        res_options = await ac.options("/api/records", headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET"
        })
        # The OPTIONS preflight MUST return 200 OK with the allowed origin
        assert res_options.status_code == 200
        assert "access-control-allow-origin" in res_options.headers
        assert res_options.headers["access-control-allow-origin"] == "http://localhost:5173"
