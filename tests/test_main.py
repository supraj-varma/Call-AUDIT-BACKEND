import json
import os
import sys

# Ensure the app can find the routes natively
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from main import app, get_cached_job_id, set_cached_job_id, CACHE_REGISTRY_PATH, JOBS_DIR

def test_app_configuration():
    # Verify the core app was instantiated with the correct title
    assert app.title == "AI Transcriber API - Production Ready"

def test_jobs_directories_exist():
    # Since main.py runs os.makedirs on module load, JOBS_DIR should exist
    assert os.path.exists(JOBS_DIR)

def test_caching_mechanism():
    # Clear cache before test if exists
    if os.path.exists(CACHE_REGISTRY_PATH):
        os.remove(CACHE_REGISTRY_PATH)
        
    test_hash = "fake_file_hash_123"
    test_job_id = "job_9999_xyz"
    
    # Ensure it returns None for an unknown file
    assert get_cached_job_id(test_hash) is None
    
    # Save the job ID
    set_cached_job_id(test_hash, test_job_id)
    
    # Ensure it retrieves it successfully
    assert get_cached_job_id(test_hash) == test_job_id
    
    # Cleanup
    if os.path.exists(CACHE_REGISTRY_PATH):
        os.remove(CACHE_REGISTRY_PATH)
