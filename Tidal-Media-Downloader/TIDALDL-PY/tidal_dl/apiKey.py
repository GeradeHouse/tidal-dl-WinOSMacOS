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
import requests

__KEYS_JSON__ = """
{
    "version": "1.0.1",
    "keys": [
        {
            "platform": "Fire TV",
            "formats": "Normal/High/HiFi(No Master)",
            "clientId": "OmDtrzFgyVVL6uW56OnFA2COiabqm",
            "clientSecret": "zxen1r3pO0hgtOC7j6twMo9UAqngGrmRiWpV7QC1zJ8=",
            "valid": "False",
            "from": "Fokka-Engineering (https://github.com/Fokka-Engineering/libopenTIDAL/blob/655528e26e4f3ee2c426c06ea5b8440cf27abc4a/README.md#example)"
        },
        {
            "platform": "Fire TV",
            "formats": "Master-Only(Else Error)",
            "clientId": "7m7Ap0JC9j1cOM3n",
            "clientSecret": "vRAdA108tlvkJpTsGZS8rGZ7xTlbJ0qaZ2K9saEzsgY=",
            "valid": "True",
            "from": "Dniel97 (https://github.com/Dniel97/RedSea/blob/4ba02b88cee33aeb735725cb854be6c66ff372d4/config/settings.example.py#L68)"
        },
        {
            "platform": "Android TV",
            "formats": "Normal/High/HiFi(No Master)",
            "clientId": "Pzd0ExNVHkyZLiYN",
            "clientSecret": "W7X6UvBaho+XOi1MUeCX6ewv2zTdSOV3Y7qC3p3675I=",
            "valid": "False",
            "from": ""
        },
        {
            "platform": "TV",
            "formats": "Normal/High/HiFi/Master",
            "clientId": "8SEZWa4J1NVC5U5Y",
            "clientSecret": "owUYDkxddz+9FpvGX24DlxECNtFEMBxipU0lBfrbq60=",
            "valid": "False",
            "from": "morguldir (https://github.com/morguldir/python-tidal/commit/50f1afcd2079efb2b4cf694ef5a7d67fdf619d09)"
        },
        {
            "platform": "Android Auto",
            "formats": "Normal/High/HiFi/Master",
            "clientId": "zU4XHVVkc2tDPo4t",
            "clientSecret": "VJKhDFqJPqvsPVNBV6ukXTJmwlvbttP7wlMlrc72se4=",
            "valid": "True",
            "from": "1nikolas (https://github.com/yaronzz/Tidal-Media-Downloader/pull/840)"
        }
    ]
}
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


# Load from gist
try:
    respond = requests.get(
        "https://api.github.com/gists/48d01f5a24b4b7b37f19443977c22cd6", timeout=10
    )
    if respond.status_code == 200:
        content = respond.json()["files"]["tidal-api-key.json"]["content"]
        _api_keys = json.loads(content)
except Exception:
    pass
