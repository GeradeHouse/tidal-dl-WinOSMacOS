#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   __init__.py
@Time    :   2020/11/08
@Author  :   Yaronzz
@Modified by: GeradeHouse
@Version :   3.0
@Contact :   yaronhuang@foxmail.com
@Desc    :   Initialization of tidal_dl package
"""
# Import necessary components for package-level access
from tidal_dl.events import *
from tidal_dl.settings import SETTINGS, TOKEN
from tidal_dl.paths import getSettingsFilePath, getTokenPath
from tidal_dl.printf import Printf
from tidal_dl.tidal import TIDAL_API, AudioQuality, Type
from tidal_dl.download import downloadTracks as start
from tidal_dl import apiKey
from tidal_dl import login

__all__ = [
    "SETTINGS",
    "TOKEN",
    "TIDAL_API",
    "Printf",
    "apiKey",
    "login",
    "start",
    "AudioQuality",
    "Type",
    "getSettingsFilePath",
    "getTokenPath",
]