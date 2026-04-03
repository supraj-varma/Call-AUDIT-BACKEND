import os
import time
import json
import requests
import io
import logging
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from dotenv import load_dotenv

# --- CONFIGURATION ---
# 1. Paste your Google Drive Folder ID here:
FOLDER_ID = "1qdGafofjjCu_smJZf2f7w11d1A-3dYeO"

# 2. Project settings
BACKEND_URL = "http://localhost:8000/api/transcribe"
PROCESSED_LOG = "processed_files.json"
SERVICE_ACCOUNT_FILE = "service_account.json"

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] Watcher: %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()

def get_drive_service():
    """Handles Google Drive Service Account authentication."""
    scopes = ['https://www.googleapis.com/auth/drive.readonly']
    
    sa_key_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")
    if sa_key_json:
        try:
            import json
            sa_info = json.loads(sa_key_json)
            creds = service_account.Credentials.from_service_account_info(
                sa_info, scopes=scopes)
            return build('drive', 'v3', credentials=creds)
        except Exception as e:
            logger.error(f"Failed to authenticate using GOOGLE_SERVICE_ACCOUNT_KEY: {e}")

    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        raise FileNotFoundError(f"'{SERVICE_ACCOUNT_FILE}' not found! Please download the JSON key from Google Cloud Console and place it in the backend folder or set GOOGLE_SERVICE_ACCOUNT_KEY env var.")

    # Use the Service Account Key file directly
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=scopes)
            
    return build('drive', 'v3', credentials=creds)

def start_polling():
    """Main loop to check for new files in Google Drive."""
    try:
        service = get_drive_service()
    except Exception as e:
        logger.error(f"Authentication Failed: {e}")
        return

    # Load previously processed files
    if os.path.exists(PROCESSED_LOG):
        try:
            with open(PROCESSED_LOG, 'r') as f:
                processed = json.load(f)
        except:
            processed = []
    else:
        processed = []

    logger.info(f"Monitoring Drive Folder (Service Account): {FOLDER_ID}")
    logger.info("Polling every 60 seconds... (Press Ctrl+C to stop)")

    while True:
        try:
            # Query for audio files in the folder
            query = f"'{FOLDER_ID}' in parents and (mimeType contains 'audio/' or name contains '.mp3' or name contains '.wav' or name contains '.m4a')"
            results = service.files().list(q=query, fields="files(id, name, createdTime)").execute()
            files = results.get('files', [])

            for file in files:
                file_id = file['id']
                file_name = file['name']
                
                if file_id not in processed:
                    logger.info(f"Found new file: {file_name}")
                    
                    # 1. Download file content into memory
                    request = service.files().get_media(fileId=file_id)
                    file_io = io.BytesIO()
                    downloader = MediaIoBaseDownload(file_io, request)
                    done = False
                    while not done:
                        _, done = downloader.next_chunk()

                    # 2. Prepare payload for FastAPI
                    file_io.seek(0)
                    files_payload = {'file': (file_name, file_io, 'audio/mpeg')}
                    data_payload = {
                        'source_language': 'Telugu',
                        'counselor_name': 'Drive-Auto',
                        'customer_name': 'Drive-User',
                        'date': file.get('createdTime', '').split('T')[0]
                    }
                    
                    # 3. Send to local project API
                    logger.info(f"Sending {file_name} to transcription pipeline...")
                    try:
                        response = requests.post(BACKEND_URL, files=files_payload, data=data_payload)
                        if response.status_code == 202:
                            logger.info(f"Success! Job started for {file_name}.")
                            processed.append(file_id)
                            with open(PROCESSED_LOG, 'w') as f:
                                json.dump(processed, f)
                        else:
                            logger.error(f"Failed to start job: {response.status_code} - {response.text}")
                    except Exception as req_err:
                        logger.error(f"Could not connect to Backend: {req_err}. Make sure uvicorn is running!")

        except Exception as e:
            logger.error(f"Error during poll: {e}")

        time.sleep(60)

if __name__ == "__main__":
    start_polling()
