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
from .events import *
from .settings import SETTINGS, TOKEN
from .paths import getSettingsFilePath, getTokenPath
from .printf import Printf
from .tidal import TIDAL_API, AudioQuality, Type
from .download import downloadTracks as start
from . import apiKey
from . import login

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
