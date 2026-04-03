import os
import tempfile
import uuid
import json
import shutil
import logging
import asyncio
import subprocess
import hashlib
import time
from contextlib import asynccontextmanager
from typing import List, Dict, Optional, Any, cast, TypedDict

from models.types import CounselorStats
from models.schemas import SubtitleResult, MinimalAnalysisResult, TranscribeRequest

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks # type: ignore
from fastapi.middleware.cors import CORSMiddleware # type: ignore
from fastapi.responses import JSONResponse # type: ignore
from pydantic import BaseModel, Field # type: ignore
from tenacity import retry, stop_after_attempt, wait_exponential # type: ignore
from dotenv import load_dotenv # type: ignore

from google import genai # type: ignore
from google.genai import types # type: ignore
from supabase import create_client, Client # type: ignore

from google.oauth2 import service_account # type: ignore
from googleapiclient.discovery import build # type: ignore
from googleapiclient.http import MediaIoBaseDownload # type: ignore
import io

from routes.auth import router as auth_router # type: ignore

load_dotenv()

# ── FFmpeg path injection (Windows) ────────────────────────────
FFMPEG_BIN_DIR = os.getenv("FFMPEG_BIN_DIR", "")
if FFMPEG_BIN_DIR and os.path.isdir(FFMPEG_BIN_DIR) and FFMPEG_BIN_DIR not in os.environ.get("PATH", ""):
    os.environ["PATH"] = FFMPEG_BIN_DIR + os.pathsep + os.environ.get("PATH", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

# ── Supabase Initialization ─────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase: Optional[Client] = None

if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("Supabase client initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize Supabase client: {e}")
else:
    logger.warning("Supabase credentials missing. Falling back to local storage.")

# ── Auto-Cleanup & Lifespan ─────────────────────────────────────
async def cleanup_temp_files_task():
    while True:
        try:
            now = time.time()
            if os.path.exists(JOBS_DIR):
                for fname in os.listdir(JOBS_DIR):
                    if fname.startswith("upload_"):
                        path = os.path.join(JOBS_DIR, fname)
                        if os.path.isfile(path) and (now - os.path.getmtime(path)) > 86400: # 24 hrs
                            os.remove(path)
                            
            temp_root = tempfile.gettempdir()
            for dirname in os.listdir(temp_root):
                if dirname.startswith("job_"):
                    path = os.path.join(temp_root, dirname)
                    if os.path.isdir(path) and (now - os.path.getmtime(path)) > 86400:
                        shutil.rmtree(path, ignore_errors=True)
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
        await asyncio.sleep(3600)  # Run hourly

async def auto_sync_drive_task():
    """Background task to periodically check for new files in Google Drive."""
    while True:
        try:
            # Wait a bit before first run to let system stabilize
            await asyncio.sleep(30) 
            logger.info("Neural Ingestion: Commencing autonomous Drive scan...")
            await perform_drive_sync()
        except Exception as e:
            logger.error(f"Autonomous Sync failed: {e}")
        
        # Interval: 1 minute (60 seconds)
        await asyncio.sleep(60)

@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_task = asyncio.create_task(cleanup_temp_files_task())
    sync_task = asyncio.create_task(auto_sync_drive_task())
    yield
    cleanup_task.cancel()
    sync_task.cancel()

app = FastAPI(title="AI Transcriber API - Production Ready", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])

CHUNK_LENGTH_MS = int(os.getenv("CHUNK_LENGTH_MINUTES", "10")) * 60 * 1000  # Default 10 Minutes (High Velocity)
NEURAL_CONCURRENCY_LIMIT = 5 # Parallelize transcription streams
semaphore = asyncio.Semaphore(NEURAL_CONCURRENCY_LIMIT)
JOBS_DIR = os.path.join(tempfile.gettempdir(), "ai_transcriber_jobs")
os.makedirs(JOBS_DIR, exist_ok=True)

ANALYTICS_DIR = os.path.join(tempfile.gettempdir(), "ai_transcriber_analytics")
os.makedirs(ANALYTICS_DIR, exist_ok=True)

RECORDS_DIR = os.path.join(tempfile.gettempdir(), "ai_transcriber_records")
os.makedirs(RECORDS_DIR, exist_ok=True)

CACHE_REGISTRY_PATH = os.path.join(tempfile.gettempdir(), "ai_transcriber_cache.json")

def get_cached_job_id(file_hash: str) -> Optional[str]:
    if os.path.exists(CACHE_REGISTRY_PATH):
        try:
            with open(CACHE_REGISTRY_PATH, "r") as f:
                cache = json.load(f)
                return cache.get(file_hash)
        except:
            pass
    return None

def set_cached_job_id(file_hash: str, job_id: str):
    cache: Dict[str, str] = {}
    if os.path.exists(CACHE_REGISTRY_PATH):
        try:
            with open(CACHE_REGISTRY_PATH, "r") as f:
                cache = cast(Dict[str, str], json.load(f))
        except:
            pass
    cache[file_hash] = job_id
    try:
        with open(CACHE_REGISTRY_PATH, "w") as f:
            json.dump(cache, f)
    except:
        pass

# Pydantic schemas moved to models.schemas

# ── Job helpers ─────────────────────────────────────────────────
def get_job_file_path(job_id: str) -> str:
    return os.path.join(JOBS_DIR, f"{job_id}.json")

def get_analytics_file_path(job_id: str) -> str:
    return os.path.join(ANALYTICS_DIR, f"{job_id}.json")

def update_job_status(job_id: str, status: str, progress: int = 0,
                      result: Optional[Dict] = None, error: Optional[str] = None,
                      metadata: Optional[Dict] = None):
    job_data = {
        "job_id": job_id, "status": status, "progress": progress,
        "result": result, "error": error, "metadata": metadata or {}
    }
    os.makedirs(JOBS_DIR, exist_ok=True)
    with open(get_job_file_path(job_id), "w") as f:
        json.dump(job_data, f)
    logger.info(f"Job {job_id} → {status} ({progress}%)")


# ── Gemini calls ────────────────────────────────────────────────
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=15))
async def safe_gemini_generate(client, uploaded_file, prompt: str):
    return await client.aio.models.generate_content(
        model=os.getenv('GEMINI_MODEL', 'gemini-2.5-flash'),
        contents=[uploaded_file, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=SubtitleResult,
            temperature=0.1
        ),
    )

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=15))
async def safe_gemini_minimal_analysis(client, english_transcript: str):
    prompt = f"""You are a professional call quality auditor. Analyze this transcript.

TRANSCRIPT:
{english_transcript}

TASKS:
1. Categorize the call accurately using these STRICT rules:
   - If the caller is asking for sponsorship, funding for events, hackathon support, or college marketing collaborations, it MUST be 'Sponsorship'.
   - If it's about paying fees or balance inquiries, it's 'Fee Follow-up'.
   - If it's a new student asking about courses/joining, it's 'Lead Inquiry'.
   - If there's a problem or grievance, it's 'Complaint'.
   - Otherwise, use 'General Support'.
2. Detect customer sentiment (Positive, Neutral, Frustrated, Angry).
3. Provide constructive feedback for the counselor's tone and effectiveness.
4. Extract 3-5 specific key points discussed.
5. Create 2-3 clear action items/next steps for the counselor.
6. Write a ONE-SENTENCE executive summary that captures the core conflict or result.
7. Determine if the student is willing to join. Use these categories carefully:
   - 'Ready to Enroll': Clear commitment, ready to pay/join immediately.
   - 'Undecided (High Risk)': Sounds positive but says things like "I'll try", "Need to ask parents", "Checking other colleges", or has budget hesitation.
   - 'Undecided': General inquiry, needs more info, neutral intent.
   - 'Not Interested': Explicitly declines.
   - Note: For Sponsorship calls, this refers to if they are willing to collaborate/partner.
8. Extract the exact names of the counselor and the caller from the transcript if available.

Output ONLY the requested JSON."""
    return await client.aio.models.generate_content(
        model=os.getenv('GEMINI_MODEL', 'gemini-2.5-flash'),
        contents=[prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=MinimalAnalysisResult,
            temperature=0.1
        ),
    )


# TranscribeRequest moved to models.schemas

async def process_chunk(idx: int, chunk_file: str, start_ms: int, request: TranscribeRequest, client: Any) -> tuple[int, str, str, str]:
    async with semaphore:
        uploaded_file = None
        try:
            logger.info(f"[{request.job_id}] Uploading chunk {idx+1}/{request.total_chunks}...")
            uploaded_file = client.files.upload(file=chunk_file)
            logger.info(f"[{request.job_id}] Chunk {idx+1} uploaded. Requesting Gemini transcription...")
            seconds = start_ms // 1000
            time_str = f"{seconds//3600:02}:{(seconds%3600)//60:02}:{seconds%60:02},{start_ms%1000:03}"
            prompt = f"""You are an expert audio transcriber and translator.
Automatically detect the primary native language spoken in the attached audio chunk (Treat '{request.source_language}' as a hint, but use the actual spoken language if it differs).
Generate THREE continuous SRT subtitle flows:
1. Transcribed accurately in the newly detected original language.
2. Translated accurately into English.
3. Translated accurately into Odia.

CRITICAL: The chunk starts at {time_str} ({start_ms}ms). All timestamps must start from {time_str}.
Output ONLY the requested JSON structure."""

            response = await safe_gemini_generate(client, uploaded_file, prompt)
            logger.info(f"[{request.job_id}] Gemini response received for chunk {idx+1}.")

            # response.parsed can be None if SDK skips auto-parsing; fall back to raw text
            if response.parsed is not None:
                tel = response.parsed.telugu_srt or ""
                eng = response.parsed.english_srt or ""
                odia = response.parsed.odia_srt or ""
            else:
                if response.text:
                    raw = json.loads(response.text)
                    tel = raw.get("telugu_srt", "")
                    eng = raw.get("english_srt", "")
                    odia = raw.get("odia_srt", "")
                    logger.warning(f"[{request.job_id}] Used json fallback for chunk {idx+1}")
                else:
                    logger.warning(f"[{request.job_id}] Gemini returned empty for chunk {idx+1}, skipping")
                    tel, eng, odia = "", "", ""


            return idx, tel, eng, odia
        except Exception as e:
            logger.error(f"[{request.job_id}] Chunk {idx} failed: {e}")
            raise
        finally:
            if uploaded_file:
                try:
                    if uploaded_file.name:
                        client.files.delete(name=uploaded_file.name)
                except Exception as de:
                    logger.warning(f"File delete failed: {de}")
        return -1, "", "", ""

async def process_audio_job(request: TranscribeRequest):
    temp_dir = tempfile.mkdtemp(prefix=f"job_{request.job_id}_")
    try:
        update_job_status(request.job_id, "processing", 1)

        # 1. Get duration using ffprobe
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", request.file_path],
                capture_output=True, text=True, check=True
            )
            duration_sec = float(result.stdout.strip())
            total_duration_ms = int(duration_sec * 1000)
        except Exception as e:
            raise Exception(f"Failed to get audio duration (is ffmpeg installed?): {e}")

        duration_minutes = max(1, total_duration_ms // 60000)
        
        # 2. Split audio using ffmpeg without decoding (Zero RAM)
        update_job_status(request.job_id, "processing", 3)
        ext = os.path.splitext(request.file_path)[1]
        chunk_pattern = os.path.join(temp_dir, f"chunk_%03d{ext}")
        chunk_sec = CHUNK_LENGTH_MS // 1000
        
        try:
            subprocess.run(
                ["ffmpeg", "-i", request.file_path, "-f", "segment", "-segment_time", str(chunk_sec),
                 "-c", "copy", chunk_pattern],
                capture_output=True, check=True
            )
        except subprocess.CalledProcessError as e:
            raise Exception(f"Failed to split audio: {e.stderr}")

        # List all created chunks
        chunk_files = sorted([f for f in os.listdir(temp_dir) if f.startswith("chunk_")])
        total_chunks = len(chunk_files)
        logger.info(f"[{request.job_id}] {total_chunks} chunk(s), {duration_minutes} min total")

        client = genai.Client(api_key=request.api_key)
        
        # 3. Process chunks concurrently using GLOBAL semaphore
        tasks = []
        for idx, chunk_filename in enumerate(chunk_files):
            chunk_file_path = os.path.join(temp_dir, chunk_filename)
            start_ms = idx * CHUNK_LENGTH_MS
            tasks.append(process_chunk(idx, chunk_file_path, start_ms, request, client))
        
        # Monitor Registry for UI
        job_meta = {
            "file": request.original_filename,
            "chunks_done": 0,
            "chunks_total": total_chunks,
            "counselor": request.counselor_name,
            "customer": request.customer_name
        }
        update_job_status(request.job_id, "processing", 10, metadata=job_meta)
        
        results: List[tuple[int, str, str, str]] = [None] * total_chunks # type: ignore
        completed_count = 0
        for coro in asyncio.as_completed(tasks):
            try:
                idx, tel, eng, odia = await coro
                results[idx] = (idx, tel, eng, odia)
                completed_count += 1
                job_meta["chunks_done"] = completed_count
                progress = int(10 + (completed_count / max(1, total_chunks)) * 70)
                update_job_status(request.job_id, "processing", progress, metadata=job_meta)
            except Exception as e:
                logger.error(f"[{request.job_id}] Chunk task failed: {e}")
                raise
            
        # Sort results by idx to keep chronological order
        results.sort(key=lambda x: x[0])
        
        final_telugu_srt = ""
        final_english_srt = ""
        final_odia_srt = ""
        for _, tel, eng, odia in results:
            final_telugu_srt += tel + "\n\n"
            final_english_srt += eng + "\n\n"
            final_odia_srt += odia + "\n\n"

        def normalize_srt(srt_text: str) -> str:
            lines = srt_text.strip().split('\n')
            output_lines, seq, i = [], 1, 0
            while i < len(lines):
                line = lines[i].strip()
                if line.isdigit() and (i + 1 < len(lines) and '-->' in lines[i + 1]):
                    output_lines.append(str(seq))
                    seq += 1
                    i += 1
                else:
                    if line or (i > 0 and lines[i - 1].strip()):
                        output_lines.append(line)
                    i += 1
            return '\n'.join(output_lines)

        normalized_english = normalize_srt(final_english_srt)
        result_data = {
            "source_language": request.source_language,
            "telugu": normalize_srt(final_telugu_srt),
            "english": normalized_english,
            "odia": normalize_srt(final_odia_srt),
        }
        # ── Minimal Analysis via Gemini ─────────────────
        logger.info(f"[{request.job_id}] Starting minimal analysis…")
        try:
            analysis_resp = await safe_gemini_minimal_analysis(client, normalized_english)
            analysis_data = analysis_resp.parsed.model_dump()
        except Exception as te:
            logger.warning(f"[{request.job_id}] Minimal analysis failed: {te}")
            analysis_data = {
                "call_category": "Error",
                "sentiment": "Unknown",
                "counselor_feedback": "Analysis failed",
                "key_points": [],
                "action_items": [],
                "summary": "Could not generate summary.",
                "willing_to_join": "Maybe"
            }

        # ── Optional: Name Overrides ──
        final_counselor = request.counselor_name
        final_customer = request.customer_name
        
        ex_couns = analysis_data.pop("extracted_counselor_name", None)
        ex_cust = analysis_data.pop("extracted_customer_name", None)
        
        if ("Drive-" in final_counselor or "Unknown" in final_counselor) and ex_couns and "Unknown" not in ex_couns:
            final_counselor = ex_couns
            
        if ("Drive-" in final_customer or "Unknown" in final_customer or final_customer.isdigit()) and ex_cust and "Unknown" not in ex_cust:
            final_customer = ex_cust

        # ── Save Record ─────────────────
        record = {
            "job_id": request.job_id,
            "filename": request.original_filename,
            "date": request.date_str,
            "counselor_name": final_counselor,
            "customer_name": final_customer,
            **analysis_data,
            "transcript_english": normalized_english,
            "transcript_telugu": normalize_srt(final_telugu_srt),
            "transcript_odia": normalize_srt(final_odia_srt),
            "source_language": request.source_language,
            "created_at": time.strftime('%Y-%m-%dT%H:%M:%S'), # ISO format for DB
        }
        
        # 1. Save to Supabase (Primary)
        if supabase:
            try:
                supabase.table("records").insert(record).execute()
                logger.info(f"[{request.job_id}] Record saved to Supabase.")
            except Exception as se:
                logger.warning(f"[{request.job_id}] Supabase save failed, attempting fallback without new columns (Error: {se})")
                fallback_record = {k: v for k, v in record.items() if k not in ["transcript_telugu", "transcript_odia", "source_language"]}
                try:
                    supabase.table("records").insert(fallback_record).execute()
                    logger.info(f"[{request.job_id}] Record saved to Supabase (Fallback without source).")
                except Exception as fallback_e:
                    logger.error(f"[{request.job_id}] Supabase fallback save failed: {fallback_e}")

        # 2. Save to Local (Backup)
        os.makedirs(RECORDS_DIR, exist_ok=True)
        with open(os.path.join(RECORDS_DIR, f"{request.job_id}.json"), "w", encoding="utf-8") as rf:
            json.dump(record, rf, ensure_ascii=False, indent=2)

        update_job_status(request.job_id, "completed", 100, result=result_data)
    except Exception as e:
        logger.error(f"[{request.job_id}] {e}", exc_info=True)
        update_job_status(request.job_id, "failed", 0, error=str(e))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        try:
            os.remove(request.file_path)
        except Exception:
            pass


# ── API Endpoints ───────────────────────────────────────────────
@app.post("/api/transcribe")
async def start_transcription(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    api_key: str = Form(None),
    source_language: str = Form("Telugu"),
    date: str = Form(""),
    counselor_name: str = Form("Unknown"),
    customer_name: str = Form("Unknown"),
):
    filename = file.filename or "unknown.mp3"
    if not filename.lower().endswith(('.mp3', '.wav', '.m4a')):
        raise HTTPException(400, "Invalid file format. Use MP3, WAV, or M4A.")

    effective_api_key = api_key or os.getenv("GEMINI_API_KEY", "")
    if not effective_api_key:
        raise HTTPException(400, "Gemini API Key required. Provide it or set GEMINI_API_KEY in .env.")

    job_id = uuid.uuid4().hex
    # Default status to prepare for caching fallback
    update_job_status(job_id, "pending", 0)

    file_ext = os.path.splitext(filename)[1]
    input_path = os.path.join(JOBS_DIR, f"upload_{job_id}{file_ext}")
    
    # ── 1. File Upload & Caching Setup ─────────────────────────────────────
    hasher = hashlib.sha256()
    with open(input_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            hasher.update(chunk)
            f.write(chunk)
            
    file_hash = hasher.hexdigest()
    
    # Caching disabled for development/refinement
    # cached_job_id = get_cached_job_id(file_hash)
    # ... (skipping cache logic) ...
                        
    # ── NotCached: Proceed normally ─────────────────────────────────
    set_cached_job_id(file_hash, job_id)

    background_tasks.add_task(process_audio_job, TranscribeRequest(
        job_id=job_id, api_key=effective_api_key, # type: ignore
        file_path=input_path, original_filename=filename, # type: ignore
        source_language=source_language, # type: ignore
        date_str=date, counselor_name=counselor_name, customer_name=customer_name # type: ignore
    ))
    return JSONResponse(content={"job_id": job_id, "message": "Job started."}, status_code=202)


# ── Drive Sync Engine ──────────────────────────────────────────
def parse_drive_filename(filename: str):
    """
    Decodes the intelligence signatures from a Drive resource name.
    Expected: {c_phone}_{cust_phone}_{date}_{time}.ext
    """
    name_no_ext = os.path.splitext(filename)[0]
    parts = name_no_ext.split('_')
    if len(parts) >= 3:
        c_phone = parts[0]
        cust_phone = parts[1]
        raw_date = parts[2]
        # Format archives: 20260228 -> 2026-02-28
        if len(raw_date) == 8:
            fmt_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        else:
            fmt_date = raw_date
        return c_phone, cust_phone, fmt_date
    return None, None, None

def lookup_personnel_by_phone(phone: str):
    """Deep-scans the authority tiers for an identity match."""
    if not supabase or not phone: return None
    try:
        # Scan Members first
        res = supabase.table("members").select("name").eq("phone", phone).order("created_at", desc=True).execute()
        if res.data: return res.data[0]["name"]
        
        # Fallback to Admin Tier
        res = supabase.table("admin_users").select("name").eq("phone", phone).order("created_at", desc=True).execute()
        if res.data: return res.data[0]["name"]
    except: pass
    return None

async def perform_drive_sync(background_tasks: Optional[BackgroundTasks] = None):
    """Core synchronization logic: Identifies new files in Google Drive and triggers neural processing."""
    raw_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    
    # ── Robust ID Extraction ──
    # If the user provides a full URL, extract the alphanumeric ID
    folder_id = raw_folder_id
    if "drive.google.com/drive/folders/" in raw_folder_id:
        folder_id = raw_folder_id.split("folders/")[1].split("?")[0].split("/")[0]
        logger.info(f"Extracted Folder ID: {folder_id} from telemetry URL.")

    if not folder_id:
        logger.warning("Neural Ingestion: No Google Drive Folder ID found in registry.")
        return 0, 0
    
    # 2. Authenticate
    try:
        sa_key_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")
        sa_file = os.path.join(os.path.dirname(__file__), "service_account.json") 

        # Prefer file-based auth (more reliable in containers)
        if os.path.exists(sa_file):
            creds = service_account.Credentials.from_service_account_file(
                sa_file, scopes=['https://www.googleapis.com/auth/drive.readonly'])
            logger.info(f"Authenticated using service account file: {sa_file}")
        elif sa_key_json:
            # Clean surrounding quotes (common in container env setups)
            cleaned = sa_key_json.strip()
            if (cleaned.startswith("'") and cleaned.endswith("'")) or (cleaned.startswith('"') and cleaned.endswith('"')):
                cleaned = cleaned[1:-1]
            
            try:
                # 1. Parse the JSON string
                sa_info = json.loads(cleaned)
                
                # 2. Fix the private_key specifically (common issue: double-escaped newlines)
                if "private_key" in sa_info:
                    sa_info["private_key"] = sa_info["private_key"].replace("\\n", "\n")
                
                creds = service_account.Credentials.from_service_account_info(
                    sa_info, scopes=['https://www.googleapis.com/auth/drive.readonly'])
                logger.info("Authenticated using GOOGLE_SERVICE_ACCOUNT_KEY from environment (with newline fix).")
            except json.JSONDecodeError as je:
                logger.error(f"Failed to parse GOOGLE_SERVICE_ACCOUNT_KEY JSON: {je}. Value sample: {cleaned[:50]}...")
                return 0, 0
        else:
            logger.warning("Sync Aborted: Service account key missing (no file or env var).")
            return 0, 0
            
        service = build('drive', 'v3', credentials=creds)
    except Exception as e:
        logger.error(f"Drive Auth Failed: {e}")
        return 0, 0

    # 3. List files
    try:
        query = f"'{folder_id}' in parents and (mimeType contains 'audio/' or name contains '.mp3' or name contains '.wav' or name contains '.m4a')"
        results = service.files().list(q=query, fields="files(id, name, createdTime)").execute()
        files = results.get('files', [])
    except Exception as e:
        logger.error(f"Drive List Failed: {e}")
        return 0, 0

    effective_api_key = os.getenv("GEMINI_API_KEY", "")
    cache_path = os.path.join(tempfile.gettempdir(), "ai_transcriber_drive_cache.json")
    
    jobs_started = 0
    for file in files:
        # Check cache
        processed_drive_ids = []
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r") as f:
                    processed_drive_ids = json.load(f)
            except: pass
            
        if file['id'] in processed_drive_ids:
            continue

        # Ingestion Logic
        async def background_drive_proc(f_id=file['id'], f_name=file['name'], f_date=file['createdTime']):
            try:
                # A. Intelligence Extraction (Filename Parsing)
                c_phone, cust_phone, archive_date = parse_drive_filename(f_name)
                
                # B. Identity Verification (Lookup)
                counselor_name = lookup_personnel_by_phone(c_phone) or "Unknown Personnel"
                customer_name = cust_phone or "Unknown Entity"
                display_date = time.strftime('%Y-%m-%d')

                # C. Download
                req = service.files().get_media(fileId=f_id)
                f_io = io.BytesIO()
                downloader = MediaIoBaseDownload(f_io, req)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                
                # D. Save Temp
                j_id = uuid.uuid4().hex
                ext = os.path.splitext(f_name)[1] or ".mp3"
                tmp_path = os.path.join(JOBS_DIR, f"drive_{j_id}{ext}")
                f_io.seek(0)
                with open(tmp_path, "wb") as tp:
                    tp.write(f_io.read())
                
                # E. Start Job with Extracted Metadata
                await process_audio_job(TranscribeRequest(
                    job_id=j_id, api_key=effective_api_key, # type: ignore
                    file_path=tmp_path, original_filename=f_name, # type: ignore
                    source_language="Telugu", # type: ignore
                    date_str=display_date, # type: ignore
                    counselor_name=counselor_name, # type: ignore
                    customer_name=customer_name # type: ignore
                ))
                
                # F. Mark as processed
                curr_cache = []
                if os.path.exists(cache_path):
                    try:
                        with open(cache_path, "r") as f:
                            curr_cache = json.load(f)
                    except: pass
                curr_cache.append(f_id)
                with open(cache_path, "w") as f:
                    json.dump(curr_cache, f)
                    
            except Exception as be:
                logger.error(f"Autonomous process failed for {f_name}: {be}")

        if background_tasks:
            background_tasks.add_task(background_drive_proc)
        else:
            asyncio.create_task(background_drive_proc())
        
        jobs_started += 1

    return len(files), jobs_started

@app.post("/api/sync-drive")
async def sync_google_drive(background_tasks: BackgroundTasks):
    """Manually trigger a check for new files in the Google Drive folder."""
    found, started = await perform_drive_sync(background_tasks)
    return JSONResponse(content={
        "status": "ok", 
        "found": found, 
        "started": started,
        "message": f"Synchronized Drive registry. Detected {found} files, initialized {started} neural streams."
    })


@app.get("/api/active-jobs")
async def list_active_jobs():
    """Returns a high-fidelity registry of all currently active neural synth jobs."""
    active_jobs = []
    if os.path.exists(JOBS_DIR):
        for fname in os.listdir(JOBS_DIR):
            if fname.endswith(".json") and not fname.startswith("drive_"):
                try:
                    with open(os.path.join(JOBS_DIR, fname), "r") as f:
                        job = json.load(f)
                        if job.get("status") in ["processing", "pending"]:
                            active_jobs.append(job)
                except:
                    pass
    return JSONResponse(content=active_jobs)

@app.get("/api/status/{job_id}")
async def get_job_status(job_id: str):
    path = get_job_file_path(job_id)
    if not os.path.exists(path):
        raise HTTPException(404, "Job not found.")
    with open(path) as f:
        return JSONResponse(content=json.load(f))

@app.get("/api/records")
async def list_records():
    # 1. Try Supabase
    if supabase:
        try:
            # Fetch all records, exclude transcript for listing performance
            response = supabase.table("records").select(
                "job_id,filename,date,counselor_name,customer_name,call_category,sentiment,summary,willing_to_join,created_at"
            ).order("created_at", desc=True).execute()
            return JSONResponse(content=response.data)
        except Exception as e:
            logger.error(f"Supabase fetch failed: {e}")

    # 2. Fallback to Local
    records = []
    if os.path.isdir(RECORDS_DIR):
        for fname in os.listdir(RECORDS_DIR):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(RECORDS_DIR, fname), encoding="utf-8") as f:
                        data = json.load(f)
                        data.pop("transcript_english", None)
                        records.append(data)
                except Exception:
                    pass
    records.sort(key=lambda x: x.get("created_at", x.get("date", 0)), reverse=True)
    return JSONResponse(content=records)

@app.get("/api/records/{job_id}")
async def get_record(job_id: str):
    # 1. Try Supabase
    if supabase:
        try:
            response = supabase.table("records").select("*").eq("job_id", job_id).single().execute()
            if response.data:
                return JSONResponse(content=response.data)
        except Exception as e:
            logger.error(f"Supabase fetch single failed: {e}")

    # 2. Fallback to Local
    path = os.path.join(RECORDS_DIR, f"{job_id}.json")
    if not os.path.exists(path):
        raise HTTPException(404, "Record not found.")
    with open(path, encoding="utf-8") as f:
        return JSONResponse(content=json.load(f))


@app.delete("/api/records/{job_id}")
async def delete_record(job_id: str):
    """Admin-only: permanently delete a call record from Supabase and local storage."""
    deleted_from = []

    # 1. Delete from Supabase
    if supabase:
        try:
            supabase.table("records").delete().eq("job_id", job_id).execute()
            deleted_from.append("supabase")
        except Exception as e:
            logger.error(f"Supabase delete failed: {e}")

    # 2. Delete from local
    path = os.path.join(RECORDS_DIR, f"{job_id}.json")
    if os.path.exists(path):
        os.remove(path)
        deleted_from.append("local")

    if not deleted_from:
        raise HTTPException(404, "Record not found in any storage.")

    return JSONResponse(content={"status": "deleted", "from": deleted_from})

@app.get("/api/counselors")
async def get_counselor_analytics():
    raw_records: List[Dict[str, Any]] = []
    
    # 1. Try Supabase
    if supabase:
        try:
            response = supabase.table("records").select("*").execute()
            raw_records = response.data
        except Exception as e:
            logger.error(f"Supabase analytics fetch failed: {e}")

    # 2. Fallback/Merge with Local
    if not raw_records and os.path.isdir(RECORDS_DIR):
        for fname in os.listdir(RECORDS_DIR):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(RECORDS_DIR, fname), encoding="utf-8") as f:
                        raw_records.append(json.load(f)) # type: ignore
                except Exception:
                    pass

    # Process Stats
    stats: Dict[str, CounselorStats] = {}
    for data in raw_records:
        c_name = data.get("counselor_name", "Unknown")
        if c_name not in stats:
            stats[c_name] = CounselorStats(
                name=c_name,
                total_calls=0,
                successful_joins=0,
                sentiment_counts={"Positive": 0, "Neutral": 0, "Frustrated": 0, "Angry": 0},
                categories={},
                total_feedback=[],
                call_history=[]
            )
        
        curr = stats[c_name]
        curr["total_calls"] += 1
        
        # logic for success (Enrollment Status is 'Ready to Enroll')
        wto = data.get("willing_to_join", "")
        if wto == "Ready to Enroll" or wto == "Yes":
             curr["successful_joins"] += 1
        
        sentiment = data.get("sentiment", "Neutral")
        curr["sentiment_counts"][sentiment] = curr["sentiment_counts"].get(sentiment, 0) + 1
        
        cat = data.get("call_category", "Unknown")
        curr["categories"][cat] = curr["categories"].get(cat, 0) + 1
        
        if data.get("counselor_feedback"):
            curr["total_feedback"].append(str(data.get("counselor_feedback")))

        curr["call_history"].append({
            "job_id": data.get("job_id"),
            "date": data.get("date"),
            "summary": data.get("summary"),
            "sentiment": sentiment,
            "category": cat,
            "willing_to_join": data.get("willing_to_join", "Maybe")
        })
    
    return JSONResponse(content=list(stats.values()))

if __name__ == "__main__":
    import uvicorn # type: ignore
    uvicorn.run(app, host="0.0.0.0", port=8000)
