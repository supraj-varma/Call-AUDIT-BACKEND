"""
Add missing columns to Supabase 'records' table:
  - transcript_source (TEXT)
  - transcript_odia (TEXT)  
  - source_language (TEXT)

Also backfills existing records with data from local JSON files if available.
"""
import os, json, sys
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

from supabase import create_client

def run_fix():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")
        return

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)



    # Test if columns exist
    print("Testing if columns exist...")

    try:
        r = supabase.table("records").select("transcript_source").limit(1).execute()
        print("✅ transcript_source column EXISTS")
    except Exception as e:
        print(f"❌ transcript_source column MISSING: {e}")
        print("\n⚠️  You need to add columns in Supabase SQL Editor:")
        print("   Go to: https://supabase.com/dashboard → SQL Editor → Run:")
        print("""
        ALTER TABLE records ADD COLUMN IF NOT EXISTS transcript_source TEXT;
        ALTER TABLE records ADD COLUMN IF NOT EXISTS transcript_odia TEXT;
        ALTER TABLE records ADD COLUMN IF NOT EXISTS source_language TEXT;
        """)
        print("After adding columns, run this script again to backfill data.\n")
        return

    try:
        r = supabase.table("records").select("transcript_odia").limit(1).execute()
        print("✅ transcript_odia column EXISTS")
    except:
        print("❌ transcript_odia column MISSING")

    try:
        r = supabase.table("records").select("source_language").limit(1).execute()
        print("✅ source_language column EXISTS")
    except:
        print("❌ source_language column MISSING")

    # ── Step 2: Backfill from local records ──
    import tempfile
    RECORDS_DIR = os.path.join(tempfile.gettempdir(), "ai_transcriber_records")

    if not os.path.isdir(RECORDS_DIR):
        print(f"\nNo local records found at {RECORDS_DIR}")
        return

    print(f"\nScanning local records in {RECORDS_DIR}...")
    local_files = [f for f in os.listdir(RECORDS_DIR) if f.endswith(".json")]
    print(f"Found {len(local_files)} local records")

    updated = 0
    for fname in local_files:
        try:
            with open(os.path.join(RECORDS_DIR, fname), encoding="utf-8") as f:
                data = json.load(f)
            
            job_id = data.get("job_id")
            if not job_id:
                continue
                
            ts = data.get("transcript_source", "")
            to = data.get("transcript_odia", "")
            sl = data.get("source_language", "")
            
            if ts or to or sl:
                update_data = {}
                if ts: update_data["transcript_source"] = ts
                if to: update_data["transcript_odia"] = to
                if sl: update_data["source_language"] = sl
                
                try:
                    supabase.table("records").update(update_data).eq("job_id", job_id).execute()
                    updated += 1
                    print(f"  ✅ Updated {job_id[:12]}... (source={bool(ts)}, odia={bool(to)}, lang={sl})")
                except Exception as ue:
                    print(f"  ❌ Failed to update {job_id[:12]}...: {ue}")
        except Exception as e:
            print(f"  ⚠️  Error reading {fname}: {e}")

    print(f"\n🎉 Done! Updated {updated}/{len(local_files)} records in Supabase.")

if __name__ == "__main__":
    run_fix()
