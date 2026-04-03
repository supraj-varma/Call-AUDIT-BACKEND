import os
import sys
from supabase import create_client, Client
from dotenv import load_dotenv
from passlib.context import CryptContext

load_dotenv()

URL = os.getenv("SUPABASE_URL")
KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# supabase: Client = create_client(URL, KEY) # Removed module-level initialization
# pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

from supabase import create_client

def run_setup():
    from passlib.context import CryptContext
    
    if not URL or not KEY:
        print("❌ Error: Missing SUPABASE config.")
        return # Prevent exit on import

    supabase = create_client(URL, KEY)
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    print("\n👑 CREATE YOUR STANDALONE DB MASTER ADMIN ACCOUNT")

    print("-" * 35)
    email = input("Enter your Admin Email: ")
    password = input("Enter your Admin Password: ")
    name = input("Enter your Admin Name: ")
    
    try:
        print("\n[1/1] Storing directly into your `admin_users` table natively...")
        
        hashed_password = pwd_context.hash(password)
        
        # We do NOT use Supabase Auth anymore, we natively inject the mapped password right onto the table!
        res = supabase.table("admin_users").insert({
            "email": email,
            "password": hashed_password,
            "name": name
        }).execute()
        
        print("\n✅ SUCCESS! Master Admin physically created exactly in the admin_users table.\n")
    
    except Exception as e:
        print(f"\n❌ FAILED: {str(e)}")

if __name__ == "__main__":
    run_setup()
