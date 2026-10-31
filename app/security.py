import secrets
from datetime import datetime,timezone,timedelta
from argon2 import PasswordHasher
ph=PasswordHasher()
def hash_password(v): return ph.hash(v)
def verify_password(h,v):
    try:return ph.verify(h,v)
    except:return False
def new_session_id():return secrets.token_urlsafe(32)
def new_csrf_token():return secrets.token_urlsafe(24)
def now():return datetime.now(timezone.utc)
def iso(d=None):return (d or now()).isoformat()
def session_expiry():return (now()+timedelta(hours=12)).isoformat()
