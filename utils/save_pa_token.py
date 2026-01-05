from datetime import datetime, timedelta
from aiohttp import ClientSession, TCPConnector
import os
import json
import ssl

CIPHER: str = 'AES128-SHA:AES256-SHA:AES256-SHA256'
CONTENT = ssl._create_unverified_context()
CONTENT.set_ciphers(CIPHER)


class PaTokenManager:
    def __init__(self):
        self.token = None
        self.expiry = None
        self.last_refresh = None
        self.user_question = None
        self.form_info = None
        self.work_form_number = None

    async def get_token(self, force_refresh=False):
        """
        获取 token，如果过期或不存在则重新获取
        """
        # 如果 token 不存在或已过期，或者强制刷新
        if not self.token or self.is_expired() or force_refresh:
            await self.refresh_token()

        return self.token

    def is_expired(self):
        """检查 token 是否过期"""
        if not self.expiry:
            return True

        # 提前5分钟刷新，避免使用过程中过期
        return datetime.now() > (self.expiry - timedelta(minutes=5))

    async def refresh_token(self):
        """调用第三方 API 获取新的 token"""
        pa_token_url: str = '/basic/openapi/auth/v1/api-key/token'
        data: dict[str, str] = {
            'ak': os.getenv("AK"),
            'sk': os.getenv("SK"),
        }
        data_json = json.dumps(data).encode('utf-8')
        connector = TCPConnector(ssl=CONTENT)
        header = {
            'Content-Type': 'application/json',
        }
        async with ClientSession(connector=connector, headers=header) as session:
            async with session.post(os.getenv('PA_BASE_URL') + pa_token_url, data=data_json) as resp:
                if resp.status == 200:
                    res_json = await resp.json()
                    self.token = res_json['data']['token']
                    self.expiry = datetime.now() + timedelta(seconds=86400)
                    self.last_refresh = datetime.now()
                    return self.token
                return None
