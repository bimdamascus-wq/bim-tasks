import os
import re
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
CA_BUNDLE = ROOT / "windows_ca_bundle.pem"

def _token():
    return os.environ.get("TELEGRAM_BOT_TOKEN","").strip()

def _verify():
    return str(CA_BUNDLE) if CA_BUNDLE.exists() else True

def migrate_chat(old, new):
    """A Telegram group upgraded to a supergroup gets a NEW chat id; the old one answers every send with 400
    «group chat was upgraded to a supergroup chat» + parameters.migrate_to_chat_id (seen 2026-11-10: the follow-up
    group lost every message from that morning). Point everything stored at the new id. Never raises."""
    old, new = str(old), str(new)
    if not old or not new or old == new:
        return False
    try:
        from .db import connect
        c = connect()
        try:
            n = c.execute("UPDATE telegram_groups SET chat_id=?,updated_at=CURRENT_TIMESTAMP WHERE chat_id=?", (new, old)).rowcount
            c.execute("UPDATE telegram_outbox SET chat_id=?,updated_at=CURRENT_TIMESTAMP WHERE chat_id=? AND status IN ('pending','retrying')", (new, old))
            import json
            from .security_center import append_audit
            append_audit(c, {'user_id': None, 'username_snapshot': 'telegram', 'action': 'telegram_group_migrated',
                             'entity_type': 'telegram_group', 'entity_id': None, 'before_json': json.dumps({'chat_id': old}),
                             'after_json': json.dumps({'chat_id': new, 'groups': n}), 'reason': None, 'gateway': 'telegram', 'ip_address': None})
            return True
        finally:
            c.close()
    except Exception:
        return False

def _migrated_to(r):
    try:
        return ((r.get("raw") or {}).get("parameters") or {}).get("migrate_to_chat_id")
    except AttributeError:
        return None

def _api(method:str, payload=None, timeout=20):
    r = _api_once(method, payload, timeout)
    new = None if r.get("ok") else _migrated_to(r)
    if new and payload and payload.get("chat_id") is not None:
        migrate_chat(payload["chat_id"], new)
        if method != "editMessageText":  # old message ids do not exist in the supergroup
            r = _api_once(method, {**payload, "chat_id": new}, timeout)
    return r

def _api_once(method:str, payload=None, timeout=20):
    token = _token()
    if not token:
        return {"ok":False,"error":"TELEGRAM_BOT_TOKEN غير مضبوط"}
    try:
        with httpx.Client(verify=_verify(), timeout=timeout) as client:
            r = client.post(f"https://api.telegram.org/bot{token}/{method}", json=payload or {})
        data = r.json()
        if not data.get("ok"):
            return {"ok":False,"error":str(data),"raw":data}
        return {"ok":True,"result":data.get("result"),"raw":data}
    except Exception as e:
        return {"ok":False,"error":str(e).replace(token,"[REDACTED]")}

def send(chat_id:str,text:str,reply_markup=None,parse_mode=None):
    if not chat_id:
        return {"ok":False,"error":"لا يوجد Telegram Chat ID"}
    if len(text)>4000:
        chunks=[text[i:i+4000] for i in range(0,len(text),4000)]
        for chunk in chunks[:-1]:
            r=send(chat_id,chunk,parse_mode=parse_mode)
            if not r.get("ok"):return r
        text=chunks[-1]
    payload={"chat_id":chat_id,"text":text}
    if reply_markup is not None:
        payload["reply_markup"]=reply_markup
    if parse_mode:
        payload["parse_mode"]=parse_mode
    r=_api("sendMessage",payload)
    if r.get("ok"):
        result=r.get("result") or {}
        return {"ok":True,"error":None,"message_id":result.get("message_id"),"result":result}
    return {"ok":False,"error":r.get("error")}

def answer_callback(callback_query_id:str,text:str="",show_alert:bool=False):
    return _api("answerCallbackQuery",{"callback_query_id":callback_query_id,"text":text,"show_alert":show_alert})

def edit_message(chat_id:str,message_id:int,text:str,reply_markup=None,parse_mode=None):
    payload={"chat_id":chat_id,"message_id":message_id,"text":text}
    if parse_mode:
        payload["parse_mode"]=parse_mode
    if reply_markup is not None:
        payload["reply_markup"]=reply_markup
    return _api("editMessageText",payload)

def get_me():
    return _api("getMe",{})

def set_webhook(url:str,secret_token:str):
    payload={"url":url,"allowed_updates":["message","callback_query"],"drop_pending_updates":False}
    if secret_token:
        payload["secret_token"]=secret_token
    return _api("setWebhook",payload)

def get_webhook_info():
    return _api("getWebhookInfo",{})
