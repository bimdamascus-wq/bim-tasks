import os, json, subprocess
from pathlib import Path

CURL = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "curl.exe"
ROOT = Path(r"C:\BIM_Tasks_Server")

def _token():
    return os.environ.get("TELEGRAM_BOT_TOKEN","").strip()

def _curl_upload(method:str, chat_id:str, file_path:Path, field_name:str, caption:str=""):
    token=_token()
    if not token:
        return {"ok":False,"error":"TELEGRAM_BOT_TOKEN غير مضبوط"}
    if not CURL.exists():
        return {"ok":False,"error":"Windows curl.exe غير موجود"}
    if not file_path.exists():
        return {"ok":False,"error":"الملف غير موجود على السيرفر"}
    url=f"https://api.telegram.org/bot{token}/{method}"
    args=[
        str(CURL),"-sS","--fail-with-body","--max-time","90",
        "-X","POST",url,
        "-F",f"chat_id={chat_id}",
        "-F",f"{field_name}=@{str(file_path)}"
    ]
    if caption:
        args += ["-F", f"caption={caption[:1024]}"]
    try:
        p=subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
            timeout=100
        )
        raw=(p.stdout or "").strip()
        if not raw:
            return {"ok":False,"error":(p.stderr or "").strip() or f"curl exit {p.returncode}"}
        try:
            data=json.loads(raw)
        except Exception:
            return {"ok":False,"error":f"Telegram response غير مفهوم: {raw[:500]}"}
        if not data.get("ok"):
            return {"ok":False,"error":data.get("description") or str(data),"raw":data}
        result=data.get("result") or {}
        return {"ok":True,"message_id":result.get("message_id"),"result":result}
    except Exception as e:
        return {"ok":False,"error":str(e)}

def send_attachment(chat_id:str, file_path, mime_type:str="", caption:str=""):
    p=Path(file_path)
    mime=(mime_type or "").lower()
    if mime.startswith("audio/") or p.suffix.lower() in {".mp3",".m4a",".ogg",".oga",".opus",".wav",".aac",".amr",".flac",".wma",".weba",".caf",".aiff",".aif",".3gp"}:
        return {"ok":False,"error":"التسجيلات الصوتية غير مقبولة"}
    # Use Telegram photo delivery for common images, document for everything else.
    if mime.startswith("image/") and p.suffix.lower() in {".jpg",".jpeg",".png",".webp"}:
        r=_curl_upload("sendPhoto",str(chat_id),p,"photo",caption)
        if r.get("ok"):
            return r
        # Fallback as document if Telegram rejects image format/size.
    return _curl_upload("sendDocument",str(chat_id),p,"document",caption)
