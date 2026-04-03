import pytest
from unittest.mock import patch, MagicMock
from datetime import timedelta
import os
import sys

# Add backend to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from auth import hash_password, verify_password, create_access_token

def test_password_hashing():
    password = "secret_password"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed) is True
    assert verify_password("wrong_password", hashed) is False

def test_jwt_creation():
    data = {"sub": "user_123", "role": "admin"}
    token = create_access_token(data, expires_delta=timedelta(minutes=15))
    assert isinstance(token, str)
    assert len(token) > 0

@patch('auth.os.getenv', return_value="test_secret")
def test_jwt_content_decoding(mock_getenv):
    import jwt
    data = {"sub": "user_id_456"}
    token = create_access_token(data)
    decoded = jwt.decode(token, "test_secret", algorithms=["HS256"])
    assert decoded["sub"] == "user_id_456"
