import json
import ssl
from aiohttp import ClientSession,TCPConnector, ClientTimeout

CIPHER: str = 'AES128-SHA:AES256-SHA:AES256-SHA256'
CONTENT = ssl._create_unverified_context()
CONTENT.set_ciphers(CIPHER)


async def request_pa(url, data, token):
    """
    :param token:
    :param data:
    :param url:
    :return:
    """
    data_json = json.dumps(data).encode('utf-8')
    connector = TCPConnector(ssl_context=CONTENT)
    timeout = ClientTimeout(total=180)
    async with ClientSession(connector=connector, timeout=timeout) as session:
        res = await session.post(
            url,
            data=data_json,
            headers={
                'Content-Type': 'application/json',
                'token': token
            },
        )
        res_json = await res.json()
    return res_json
