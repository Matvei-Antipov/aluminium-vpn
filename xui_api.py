import os
import uuid
from py3xui import AsyncApi, Client
from config import (
    PANEL_URL, PANEL_USERNAME, PANEL_PASSWORD, INBOUND_ID,
    SERVER_IP, SERVER_PORT, REALITY_PK, SNI, SID, logger
)

vpn_api: AsyncApi | None = None

async def init_vpn_api():
    global vpn_api
    vpn_api = AsyncApi(
        host=PANEL_URL,
        username=PANEL_USERNAME,
        password=PANEL_PASSWORD,
        use_tls_verify=False,
    )
    try:
        await vpn_api.login()
        logger.info("✅ X-UI API connected")
    except Exception as e:
        logger.warning(f"⚠️ X-UI login failed: {e}")

async def add_client_via_xui_api(uuid_str: str, email: str, limit_ip: int = 1, expiry_time: int = 0) -> bool:
    if vpn_api is None:
        raise RuntimeError("vpn_api is not initialized")

    await vpn_api.login()

    LIMIT_GB = 75 
    LIMIT_BYTES = LIMIT_GB * 1024 * 1024 * 1024

    client = Client(
        id=uuid_str,
        email=email,
        enable=True,
        limit_ip=limit_ip,
        total_gb=LIMIT_BYTES,
        expiry_time=expiry_time,
        flow="xtls-rprx-vision",
        tg_id="",
        sub_id="",
    )

    await vpn_api.client.add(inbound_id=INBOUND_ID, clients=[client])
    logger.info("✅ Client %s added successfully via py3xui", email)
    return True

async def update_client_via_xui_api(uuid_str: str, email: str, expiry_time: int) -> bool:
    if vpn_api is None: raise RuntimeError("vpn_api is not initialized")
    await vpn_api.login()

    LIMIT_GB = 75 
    LIMIT_BYTES = LIMIT_GB * 1024 * 1024 * 1024

    client = Client(
        id=uuid_str,
        email=email,
        enable=True,
        limit_ip=1,
        total_gb=LIMIT_BYTES,
        expiry_time=expiry_time,
        flow="xtls-rprx-vision",
        tg_id="",
        sub_id="",
    )

    try:
        await vpn_api.client.update(uuid_str, client=client)
        logger.info(f"✅ Client {email} updated successfully")
        return True
    except Exception as e:
        logger.warning(f"⚠️ Ошибка обновления {email}: {e}. Пробуем пересоздать...")

        try:
            inbounds = await vpn_api.inbound.get_list()
            target_inbound = next((i for i in inbounds if i.id == INBOUND_ID), None)

            if target_inbound:
                clients = target_inbound.settings.clients
                existing_client = next((c for c in clients if c.email == email), None)

                if existing_client:
                    real_uuid = existing_client.id
                    logger.info(f"🧟‍♂️ Удаляем зависшего клиента {real_uuid}...")
                    try:
                        await vpn_api.client.delete(INBOUND_ID, real_uuid)
                    except Exception: pass
            logger.info(f"🆕 Создаем клиента {email} заново...")
            await add_client_via_xui_api(uuid_str, email, limit_ip=1, expiry_time=expiry_time)
            return True

        except Exception as deep_error:
            logger.error(f"❌ Не удалось восстановить клиента {email}: {deep_error}")
            raise deep_error

def generate_vless_link(user_uuid: str, email: str) -> str:
    return (
        f"vless://{user_uuid}@{SERVER_IP}:{SERVER_PORT}?"
        f"security=reality&encryption=none&pbk={REALITY_PK}&fp=chrome&type=tcp&flow=xtls-rprx-vision&"
        f"sni={SNI}&sid={SID}#{email}"
    )