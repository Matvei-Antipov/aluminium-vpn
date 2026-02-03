import os
import json
import hmac
import hashlib
import aiohttp
import logging

LAVA_PROJECT_ID = os.getenv("LAVA_PROJECT_ID")
LAVA_SECRET_KEY = os.getenv("LAVA_SECRET_KEY")

LAVA_CREATE_URL = "https://api.lava.ru/business/invoice/create"
LAVA_STATUS_URL = "https://api.lava.ru/business/invoice/status"

logger = logging.getLogger(__name__)

def generate_signature(json_string: str, secret_key: str) -> str:
    return hmac.new(
        bytes(secret_key, 'UTF-8'),
        bytes(json_string, 'UTF-8'),
        hashlib.sha256
    ).hexdigest()

async def create_lava_invoice(amount: float, order_id: str, comment: str = "VPN Access"):
    
    if not LAVA_PROJECT_ID or "ВАШ_" in LAVA_PROJECT_ID:
        logger.error("ОШИБКА: Впишите Secret Key в файл lava_pay.py!")
        return {"status": "error", "message": "No Keys"}

    data = {
        "shopId": LAVA_PROJECT_ID,
        "sum": float(amount),
        "orderId": order_id,
        "expire": 300, 
        "comment": f"{comment}: {order_id}",
        "hookUrl": "https://google.com", 
        "failUrl": "https://google.com",
        "successUrl": "https://google.com"
    }

    sorted_data = dict(sorted(data.items()))

    json_str = json.dumps(sorted_data, separators=(',', ':'), ensure_ascii=False)

    signature = generate_signature(json_str, LAVA_SECRET_KEY)
    
    headers = {
        "Signature": signature,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(LAVA_CREATE_URL, data=json_str, headers=headers) as resp:
                text_response = await resp.text()
                try:
                    return json.loads(text_response)
                except:
                    logger.error(f"Lava Create Error {resp.status}: {text_response}")
                    return {"status": "error", "message": f"HTTP {resp.status}"}
                    
    except Exception as e:
        logger.error(f"Lava connection error: {e}")
        return {"status": "error", "message": str(e)}

async def check_lava_status(order_id: str, invoice_id: str):
    """Проверка статуса (Lava Business)"""
    if not LAVA_PROJECT_ID or not LAVA_SECRET_KEY:
        return {"status": "error"}

    data = {
        "shopId": LAVA_PROJECT_ID,
        "invoiceId": invoice_id,
        "orderId": order_id
    }

    sorted_data = dict(sorted(data.items()))

    json_str = json.dumps(sorted_data, separators=(',', ':'), ensure_ascii=False)

    signature = generate_signature(json_str, LAVA_SECRET_KEY)
    
    headers = {
        "Signature": signature,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(LAVA_STATUS_URL, data=json_str, headers=headers) as resp:
                text_response = await resp.text()
                
                try:
                    result = json.loads(text_response)
                    if result.get("status") == 200:
                        inner_status = result.get("data", {}).get("status")
                        logger.info(f"Lava Status Check: {inner_status}")
                    else:
                        logger.error(f"Lava Status Error: {result}")
                    return result
                except:
                    logger.error(f"Lava Status Parse Error {resp.status}: {text_response}")
                    return {"status": "error", "message": f"HTTP {resp.status}"}
                    
    except Exception as e:
        logger.error(f"Lava status check error: {e}")
        return {"status": "error", "message": str(e)}