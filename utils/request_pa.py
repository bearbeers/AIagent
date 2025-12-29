import json
import ssl
import urllib.request

CIPHER: str = 'AES128-SHA:AES256-SHA:AES256-SHA256'
CONTENT = ssl._create_unverified_context()
CONTENT.set_ciphers(CIPHER)


def request_pa(url, data, token):
    """
    :param token:
    :param data:
    :param url:
    :return:
    """
    data_json = json.dumps(data).encode('utf-8')
    req = urllib.request.Request(
        url=url,
        data=data_json,
        headers={
            "token": token,
            'Content-Type': 'application/json'
        },
        method='POST'
    )

    res = urllib.request.urlopen(req, context=CONTENT)
    res_data = res.read().decode('utf-8')
    res_json = json.loads(res_data)
    return res_json
