#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :  apiKey.py
@Date    :  2021/11/30
@Author  :  Yaronzz
@Modified by: GeradeHouse
@Version :  3.0
@Contact :  yaronhuang@foxmail.com
@Desc    :
"""
import json
import base64

# Atmos TV key reported by Tidal-Web-Downloader users as supporting currently working playback.
ATMOS_TV_CLIENT_ID = "4N3n6Q1x95LL5K7p"
ATMOS_TV_CLIENT_SECRET = "oKOXfJW371cX6xaZ0PyhgGNBdNLlBZd4AKKYougMjik="

# Existing streamrip/desktop key retained as a selectable fallback profile.
STREAMRIP_CLIENT_ID = base64.b64decode("ZlgySnhkbW50WldLMGl4VA==").decode("iso-8859-1")
STREAMRIP_CLIENT_SECRET = base64.b64decode(
    "MU5tNUFmREFqeHJnSkZKYktOV0xlQXlLR1ZHbUlOdVhQUExIVlhBdnhBZz0=",
).decode("iso-8859-1")

__KEYS_JSON__ = f"""
{{
    "version": "2026.04.26",
    "keys": [
        {{
            "platform": "Atmos TV (Tidal-Web-Downloader)",
            "formats": "Normal/High/HiFi/Atmos",
            "clientId": "{ATMOS_TV_CLIENT_ID}",
            "clientSecret": "{ATMOS_TV_CLIENT_SECRET}",
            "valid": "True",
            "from": "Tidal-Web-Downloader forum/source reference"
        }},
        {{
            "platform": "Desktop (streamrip)",
            "formats": "Low/High/HiFi/Max",
            "clientId": "{STREAMRIP_CLIENT_ID}",
            "clientSecret": "{STREAMRIP_CLIENT_SECRET}",
            "valid": "True",
            "from": "streamrip project (https://github.com/omnunum/streamrip/)"
        }}
    ]
}}
"""
_api_keys = json.loads(__KEYS_JSON__)
__ERROR_KEY__ = {
    "platform": "None",
    "formats": "",
    "clientId": "",
    "clientSecret": "",
    "valid": "False",
}


def getNum():
    return len(_api_keys["keys"])


def getItem(index: int):
    if index < 0 or index >= len(_api_keys["keys"]):
        return __ERROR_KEY__
    return _api_keys["keys"][index]


def isItemValid(index: int):
    item = getItem(index)
    return item["valid"] == "True"


def getItems():
    return _api_keys["keys"]


def getLimitIndexs():
    return [str(i) for i in range(len(_api_keys["keys"]))]


def getVersion():
    return _api_keys["version"]


# Load from gist (can be disabled with TIDALDL_DISABLE_REMOTE_KEYS=1)
# import os
# if os.getenv("TIDALDL_DISABLE_REMOTE_KEYS", "0") != "1":
#     try:
#         respond = requests.get(
#             "https://api.github.com/gists/48d01f5a24b4b7b37f19443977c22cd6", timeout=10
#         )
#         if respond.status_code == 200:
#             data = respond.json()
#             files = data.get("files", {{}})
#             entry = files.get("tidal-api-key.json")
#             if entry and "content" in entry:
#                 content = entry["content"]
#                 cand = json.loads(content)
#                 # Only adopt the remote keys if the structure is correct and at least one key is marked valid.
#                 if (
#                     isinstance(cand, dict)
#                     and isinstance(cand.get("keys"), list)
#                     and any(k.get("valid") == "True" for k in cand["keys"])
#                 ):
#                     _api_keys = cand
#     except Exception:
#         pass
