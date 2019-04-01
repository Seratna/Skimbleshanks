import base64
import os

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.fernet import Fernet


class WCMLMessageType(object):
    CONNECTION_REQUEST = 0x00
    CONNECTION_MADE = 0x01
    CONNECTION_LOST = 0x02
    DATA = 0x03


class BytesReader(object):
    def __init__(self, data: bytes):
        self._pointer: int = 0
        self._data: bytes = data

    def read(self, n=None):
        if n is not None:
            start = self._pointer
            self._pointer += n
            end = self._pointer
            return self._data[start: end]
        else:
            return self._data[self._pointer:]


class FernetEncryptor(object):
    def __init__(self, password):
        password_encoded = password.encode('utf-8')  # Convert to type bytes
        salt = b'salt_'  # must be of type bytes
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        key = base64.urlsafe_b64encode(kdf.derive(password_encoded))  # Can only use kdf once

        self._fernet = Fernet(key)

    def encrypt(self, message: bytes):
        return self._fernet.encrypt(message)

    def decrypt(self, message: bytes):
        return self._fernet.decrypt(message)
