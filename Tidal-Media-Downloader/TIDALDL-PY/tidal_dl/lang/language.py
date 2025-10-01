#!/usr/bin/env python
# -*- encoding: utf-8 -*-
r"""
@File    :   Tidal-Media-Downloader\TIDALDL-PY\tidal_dl\lang\language.py
@Time    :   2021/11/24
@Author  :   Yaronzz & jee019
@Modified by: GeradeHouse
@Version :   1.2
@Contact :   yaronhuang@foxmail.com
@Desc    :   Language configuration for Tidal-DL.
Only English and Dutch languages are supported.
"""
from typing import Type, Dict, Union, Any
from .dutch import LangDutch
from .english import LangEnglish

LANGUAGES: Dict[str, Type[Union[LangDutch, LangEnglish]]] = {
    "dutch": LangDutch,
    "english": LangEnglish,
}


def getLang(langName: str) -> Union[LangDutch, LangEnglish]:
    return LANGUAGES.get(langName.lower(), LangEnglish)()


class _LangProxy:
    def __init__(self):
        self._instance: Union[LangDutch, LangEnglish] = getLang("english")

    def set_language(self, lang_name: str):
        self._instance = getLang(lang_name)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._instance, name)


LANG = _LangProxy()


def setLang(langName: str) -> None:
    LANG.set_language(langName)
