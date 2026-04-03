import os
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, Header, Body # type: ignore
from pydantic import BaseModel # type: ignore
from supabase import create_client, Client # type: ignore
import bcrypt # type: ignore
import jwt # type: ignore
import datetime
import re
from dotenv import load_dotenv # type: ignore

load_dotenv()

router = APIRouter()

def get_supabase() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("Supabase credentials not found in env.")
    return create_client(url, key)

def get_jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if not secret:
        # For routes/auth.py specifically, we might want to strictly require it or provide a safe default for tests
        return os.getenv("JWT_SECRET", "test-secret-only-for-local-dev")
    return secret

# pwd_context removed, using bcrypt natively

def safe_truncate_password(pwd: str) -> str:
    """Bcrypt strictly limits passwords to 72 bytes. This securely truncates multi-byte chars."""
    return pwd.encode('utf-8')[:72].decode('utf-8', 'ignore')

def hash_password(password: str) -> str:
    safe_pwd = safe_truncate_password(password).encode('utf-8')
    return bcrypt.hashpw(safe_pwd, bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    try:
        safe_pwd = safe_truncate_password(password).encode('utf-8')
        return bcrypt.checkpw(safe_pwd, hashed.encode('utf-8'))
    except Exception:
        # Fallback for plain text or malformed hashes
        return password == hashed

def create_access_token(data: dict, expires_delta: Optional[datetime.timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.datetime.utcnow() + expires_delta
    else:
        expire = datetime.datetime.utcnow() + datetime.timedelta(hours=24)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, get_jwt_secret(), algorithm="HS256")

def enforce_strict_password(pwd: str):
    if len(pwd) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long.")
    if len(pwd) > 50:
        raise HTTPException(status_code=400, detail="Password cannot exceed 50 characters.")
    if not re.search(r'^[A-Z]', pwd):
        raise HTTPException(status_code=400, detail="Password must start with a capital letter.")
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', pwd):
        raise HTTPException(status_code=400, detail="Password must contain at least one special character.")
    if not re.search(r'[a-zA-Z]', pwd):
        raise HTTPException(status_code=400, detail="Password must contain letters.")

class LoginRequest(BaseModel):
    email: str
    password: str

class AddMemberRequest(BaseModel):
    email: str
    password: str
    name: str
    phone: Optional[str] = None

# ── Dependencies ──
async def get_current_token(authorization: str | None = Header(None)) -> str:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid or missing token")
    return str(authorization).split(" ")[1]

async def get_current_user(token: str = Depends(get_current_token)) -> Dict[str, Any]:
    try:
        # Verify custom JWT
        payload = jwt.decode(token, get_jwt_secret(), algorithms=["HS256"])
        user_id = payload.get("id")
        role = payload.get("role")
        email = payload.get("email")
        
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")
        
        name = ""
        supabase = get_supabase()
        if role == "admin":
            admin_check = supabase.table("admin_users").select("*").eq("id", user_id).execute()
            if admin_check.data:
                name = admin_check.data[0].get("name", "")
            else:
                raise HTTPException(status_code=403, detail="Admin account removed.")
        else:
            member_check = supabase.table("members").select("*").eq("id", user_id).execute()
            if member_check.data:
                name = member_check.data[0].get("name", "")
            else:
                raise HTTPException(status_code=403, detail="Member account removed.")
        
        return {
            "id": user_id,
            "email": email,
            "role": role,
            "name": name
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")

async def require_admin(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user


# ── Authentication Endpoints (NATIVE TABLE AUTHENTICATION) ──
@router.post("/login")
async def login(req: LoginRequest):
    # 1. Check Admins Table First
    supabase = get_supabase()
    admin_check = supabase.table("admin_users").select("*").eq("email", req.email).execute()
    role = "member"
    found_user = None
    
    if admin_check.data:
        found_user = admin_check.data[0]
        role = "admin"
    else:
        # 2. Check Members Table
        member_check = supabase.table("members").select("*").eq("email", req.email).execute()
        if member_check.data:
            found_user = member_check.data[0]
        else:
            raise HTTPException(status_code=401, detail="User not found.")
            
    if not verify_password(req.password, found_user["password"]):
        raise HTTPException(status_code=401, detail="Invalid password.")
        
    # Generate Custom JWT Token!
    token = create_access_token({
        "id": found_user["id"],
        "email": found_user["email"],
        "role": role
    })


    return {
        "token": token,
        "user": {
            "id": found_user["id"],
            "email": found_user["email"],
            "role": role,
            "name": found_user.get("name", "")
        }
    }


@router.get("/check-setup")
async def check_setup():
    """Returns True if no admins exist, indicating setup is needed."""
    supabase = get_supabase()
    check = supabase.table("admin_users").select("id").limit(1).execute()
    needs_setup = not (check.data and len(check.data) > 0)
    return {"needs_setup": needs_setup}


@router.post("/setup")
async def initial_setup(req: AddMemberRequest):
    enforce_strict_password(req.password)
    # This route is ONLY available if there are exactly 0 admins configured.
    supabase = get_supabase()
    check = supabase.table("admin_users").select("id").limit(1).execute()
    if check.data and len(check.data) > 0:
        raise HTTPException(status_code=403, detail="Setup already complete. An admin already exists.")
        
    try:
        hashed_password = hash_password(req.password)
        supabase = get_supabase()
        res = supabase.table("admin_users").insert({
            "email": req.email,
            "password": hashed_password,
            "name": req.name,
            "phone": req.phone
        }).execute()
        return {"message": "Master Admin successfully created!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error during setup: {str(e)}")


@router.get("/me")
async def get_me(user: Dict[str, Any] = Depends(get_current_user)):
    return user


# ── Admin Endpoints ──
@router.get("/admin/users")
async def list_users(admin: dict = Depends(require_admin)):
    try:
        # Omit returning the password column safely.
        supabase = get_supabase()
        admins = supabase.table("admin_users").select("id, email, name, phone, created_at").execute().data
        members = supabase.table("members").select("id, email, name, phone, created_at").execute().data
        return {
            "admins": admins,
            "members": members
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/admin/promote")
async def promote_user(user_id: str = Body(embed=True), admin: dict = Depends(require_admin)):
    supabase = get_supabase()
    member_res = supabase.table("members").select("*").eq("id", user_id).execute()
    if not member_res.data:
        raise HTTPException(status_code=404, detail="Member not found")
    
    m_data = member_res.data[0]
    
    supabase.table("admin_users").insert({
        "email": m_data["email"],
        "password": m_data["password"], # Carry the password securely over
        "name": m_data["name"],
        "phone": m_data.get("phone"),
        "created_by": admin["id"]
    }).execute()
    
    supabase.table("members").delete().eq("id", user_id).execute()
    
    return {"message": "User promoted to Admin."}


@router.post("/admin/demote")
async def demote_user(user_id: str = Body(embed=True), admin: dict = Depends(require_admin)):
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="You cannot demote yourself.")
        
    supabase = get_supabase()
    admin_res = supabase.table("admin_users").select("*").eq("id", user_id).execute()
    if not admin_res.data:
        raise HTTPException(status_code=404, detail="Admin not found")
    
    a_data = admin_res.data[0]
    
    supabase.table("members").insert({
        "email": a_data["email"],
        "password": a_data["password"], # Carry the password securely over
        "name": a_data["name"],
        "phone": a_data.get("phone")
    }).execute()
    
    supabase.table("admin_users").delete().eq("id", user_id).execute()
    return {"message": "User demoted to Member."}


@router.post("/admin/add-member")
async def add_member(req: AddMemberRequest, admin: dict = Depends(require_admin)):
    enforce_strict_password(req.password)
    try:
        hashed_password = hash_password(req.password)
        supabase = get_supabase()
        res = supabase.table("members").insert({
            "email": req.email,
            "password": hashed_password,
            "name": req.name,
            "phone": req.phone
        }).execute()
        
        # Pull the DB generated ID to return securely
        new_id = res.data[0]["id"] if res.data else "success"
        
        return {"message": f"Member {req.name} successfully created.", "user_id": new_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create member: {str(e)}")


@router.delete("/admin/delete-member/{member_id}")
async def delete_member(member_id: str, admin: dict = Depends(require_admin)):
    """Admin-only: permanently removes a personnel account."""
    try:
        # Check against current admin node to prevent suicide delete
        if member_id == admin.get("id"):
            raise HTTPException(status_code=400, detail="Self-deletion prohibited. Internal security protocol violation.")

        # Attempt deletion from both authority tiers
        supabase = get_supabase()
        supabase.table("members").delete().eq("id", member_id).execute()
        supabase.table("admin_users").delete().eq("id", member_id).execute()
        
        return {"message": "Personnel node successfully purged from system registry."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Core rejection on deletion: {str(e)}")
