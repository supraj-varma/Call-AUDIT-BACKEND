import pytest
from unittest.mock import patch, MagicMock
import os
import sys

# Add backend to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from drive_watcher import get_drive_service

@patch('drive_watcher.service_account.Credentials.from_service_account_file')
@patch('drive_watcher.build')
def test_get_drive_service_file_exists(mock_build, mock_creds):
    # Mock existence of service account file
    with patch('os.path.exists', return_value=True):
        service = get_drive_service()
        assert service is not None
        mock_build.assert_called_once()

def test_get_drive_service_missing_file():
    # Mock missing file
    with patch('os.path.exists', return_value=False):
        # Should raise FileNotFoundError
        with pytest.raises(FileNotFoundError):
            get_drive_service()

@patch('drive_watcher.service_account.Credentials.from_service_account_info')
@patch('drive_watcher.build')
def test_get_drive_service_env_var(mock_build, mock_creds_info):
    # Mock environment variable
    mock_env = {
        "GOOGLE_SERVICE_ACCOUNT_KEY": '{"type": "service_account", "project_id": "test"}'
    }
    with patch.dict('os.environ', mock_env):
        service = get_drive_service()
        assert service is not None
        mock_creds_info.assert_called_once()
