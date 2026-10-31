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

def _api(method:str, payload=None, timeout=20):
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

def edit_message(chat_id:str,message_id:int,text:str,reply_markup=None):
    payload={"chat_id":chat_id,"message_id":message_id,"text":text}
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
