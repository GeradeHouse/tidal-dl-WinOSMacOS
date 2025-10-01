#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File    :   decryption.py
@Time    :   2019/02/27
@Author  :   Yaron Huang
@Modified by: GeradeHouse
@Version :   1.0
@Contact :   yaronhuang@qq.com
@Desc    :   HIGH Quality Track Decryption;File From Project 'RedSea'
"""
import base64
from typing import Tuple

from Crypto.Cipher import AES
from Crypto.Util import Counter


def decrypt_security_token(security_token: str) -> Tuple[bytes, bytes]:
    """
    Decrypts security token into key and nonce pair

    security_token should match the securityToken value from the web response
    """

    # Do not change this
    master_key = "UIlTTEMmmLfGowo/UC60x2H45W6MdGgTRfo/umg4754="

    # Decode the base64 strings to ascii strings
    master_key_bytes = base64.b64decode(master_key)
    security_token_bytes = base64.b64decode(security_token)

    # Get the IV from the first 16 bytes of the securityToken
    iv = security_token_bytes[:16]
    encrypted_st = security_token_bytes[16:]

    # Initialize decryptor
    decryptor = AES.new(master_key_bytes, AES.MODE_CBC, iv)

    # Decrypt the security token
    decrypted_st = decryptor.decrypt(encrypted_st)

    # Get the audio stream decryption key and nonce from the decrypted security token
    key = decrypted_st[:16]
    nonce = decrypted_st[16:24]

    return key, nonce


def decrypt_file(efile: str, dfile: str, key: bytes, nonce: bytes):
    """
    Decrypts an encrypted MQA file given the file, key and nonce
    """

    # Initialize counter and file decryptor
    counter = Counter.new(64, prefix=nonce, initial_value=0)
    decryptor = AES.new(key, AES.MODE_CTR, counter=counter)

    # Open and decrypt
    with open(efile, "rb") as eflac:
        flac = decryptor.decrypt(eflac.read())

        # Replace with decrypted file
        with open(dfile, "wb") as dflac:
            dflac.write(flac)
