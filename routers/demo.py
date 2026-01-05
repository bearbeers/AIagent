# import time

import aiohttp
from aiohttp import ClientSession, ClientTimeout
import os
import json

# from utils.request_pa import request_pa
from dotenv import load_dotenv
import ssl


load_dotenv()
CIPHER: str = 'AES128-SHA:AES256-SHA:AES256-SHA256'
CONTENT = ssl._create_unverified_context()
CONTENT.set_ciphers(CIPHER)

# async def main():
#     url = "/basic/openapi/engine/chat/v1/completions"
#     data = {
#         "chatId": "1414934653136449536",
#         "appId": "a4f80bb2f25b4f65bd8a0fbaa813d0c9",
#         "messages": [
#             {
#                 "content": "生成处置流程和处理方案." + 'xxxx',
#                 "role": "user"
#             }
#         ]
#     }
#     start_time = time.time()
#     try:
#         res_json = request_pa(
#             'https://chatgpt.sncitybrain.com:30080' + url,
#             data,
#             token="eyJhbGciOiJIUzI1NiJ9.eyJpYXQiOjE3Njc0OTc1MzUsInN1YiI6IjE3MDIxIiwiSldUX0VOVElUWSI6eyJ0b2tlbklkIjoiOGI0NWVmZDI3NGRhNGU3Mzk4M2MwZDJjNGZkMWU3OTIiLCJ1c2VySWQiOjE3MDIxLCJlbWFpbCI6bnVsbCwib3JnSWQiOm51bGwsInVzZXJOYW1lIjpudWxsLCJ0ZW5hbnRJZCI6InpudGRzMSIsImRlZmF1bHRMYWJJZCI6bnVsbCwiYWNjZXNzS2V5IjoiYWstMTI2YzcxY2E1ZDdhNGNlZGIyMzgiLCJ0aGlyZFNvdXJjZSI6bnVsbCwidGhpcmRVc2VySWQiOm51bGwsInRoaXJkVXNlck5hbWUiOm51bGwsInRoaXJkVXNlckVtYWlsIjpudWxsLCJ0aGlyZFVzZXJUZW5hbnRJZCI6bnVsbH19.qtNHJa_MycTzMBYgFCUq-42dK0PvN7bydYZAkBV5X7M",
#             timeout=180)
#         ai_reply = res_json['choices'][0]['message']['content']
#         end_time = time.time()
#         print(end_time - start_time)
#         print(ai_reply)
#     except Exception as e:
#         print(e)
#         end_time = time.time()
#         print(end_time - start_time)


async def async_main():
    api_url: str = '/basic/openapi/auth/v1/api-key/token'
    data: dict[str, str] = {
        'ak': os.getenv("AK"),
        'sk': os.getenv("SK"),
    }
    data_json = json.dumps(data).encode('utf-8')
    connector = aiohttp.TCPConnector(ssl=CONTENT)
    header = {
        'Content-Type': 'application/json',
    }
    async with ClientSession(connector=connector, headers=header) as session:
        async with session.post(os.getenv('PA_BASE_URL') + api_url, data=data_json) as resp:
            if resp.status == 200:
                res_json = await resp.json()
                return res_json['data']['token']
            return None

async def get_complents():
    token = await async_main()
    url = "/basic/openapi/engine/chat/v1/completions"
    data = {
        "chatId": "1414934653136449537",
        "appId": "a4f80bb2f25b4f65bd8a0fbaa813d0c9",
        "messages": [
            {
                "content": "生成处置流程和处理方案." + 'xxxx',
                "role": "user"
            }
        ]
    }
    header = {
        'Content-Type': 'application/json',
        'token': token,
    }
    connector = aiohttp.TCPConnector(ssl=CONTENT)
    async with ClientSession(connector=connector, timeout=ClientTimeout(180)) as session:
        async with session.post(os.getenv('PA_BASE_URL') + url, data=json.dumps(data).encode('utf-8'), headers=header) as resp:
            if resp.status == 200:
                res_json = await resp.json()
                print(res_json['choices'][0]['message']['content'])
                return res_json['choices'][0]['message']['content']
            else:
                res_json = await resp.text()
                print(res_json)
                return None




if __name__ == '__main__':
    import asyncio

    asyncio.run(get_complents())
    # asyncio.run(main())
