
from utils.request_pa import request_pa

async def main():
    url = "/basic/openapi/engine/chat/v1/completions"
    data = {
        "chatId": "1414934653136449536",
        "appId": "a4f80bb2f25b4f65bd8a0fbaa813d0c9",
        "messages": [
            {
                "content": "生成处置流程和处理方案." + 'xxxx' ,
                "role": "user"
            }
        ]
    }

    res_json = request_pa('https://chatgpt.sncitybrain.com:30080'+ url, data, token="eyJhbGciOiJIUzI1NiJ9.eyJpYXQiOjE3NjcxODUxNTMsInN1YiI6IjE3MDIxIiwiSldUX0VOVElUWSI6eyJ0b2tlbklkIjoiZjZkNGE2YTFmZDhkNGNlY2EzMzU0MDVkYjE2M2UxMzkiLCJ1c2VySWQiOjE3MDIxLCJlbWFpbCI6bnVsbCwib3JnSWQiOm51bGwsInVzZXJOYW1lIjpudWxsLCJ0ZW5hbnRJZCI6InpudGRzMSIsImRlZmF1bHRMYWJJZCI6bnVsbCwiYWNjZXNzS2V5IjoiYWstMTI2YzcxY2E1ZDdhNGNlZGIyMzgiLCJ0aGlyZFNvdXJjZSI6bnVsbCwidGhpcmRVc2VySWQiOm51bGwsInRoaXJkVXNlck5hbWUiOm51bGwsInRoaXJkVXNlckVtYWlsIjpudWxsLCJ0aGlyZFVzZXJUZW5hbnRJZCI6bnVsbH19.BmGw2i92UBrST0LI8Vpn3-TNU0jW8p9MdGRNiTeuH1o")
    ai_reply = res_json['choices'][0]['message']['content']
    print(ai_reply)

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())