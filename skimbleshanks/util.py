import base64
import sys
from struct import pack, unpack
import time
import asyncio
import logging
import uuid

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.fernet import Fernet
import colorlog


class WCMLMessageType(object):
    CONNECTION_REQUEST = 0x00
    CONNECTION_MADE = 0x01
    CONNECTION_LOST = 0x02
    DATA = 0x03
    WEBSOCKET_PROMOTION = 0x04
    PROTOCOL_SYNC = 0x05


class WCMLMessage(object):
    """
    websocket message
    """

    def __init__(self, message_type: int, from_id: int, to_id: int, **kwargs) -> None:
        """
        Args:
            message_type: type of the message
            from_id: id of the protocol sending the message
            to_id: id of the protocol that would receive the message
        """
        self._message_type = message_type
        self._from_id = from_id
        self._to_id = to_id
        self._attr = kwargs

    @property
    def message_type(self):
        return self._message_type

    @property
    def from_id(self):
        return self._from_id

    @property
    def to_id(self):
        return self._to_id

    def bytes(self) -> bytes:
        """
        pack the message into bytes format
        """
        if self._message_type in [WCMLMessageType.CONNECTION_REQUEST,
                                  WCMLMessageType.CONNECTION_MADE]:
            host: str = self.host
            port: int = self.port
            return pack(f'!BQQB{len(host)}sH',
                        self._message_type,
                        self._from_id,
                        self._to_id,
                        len(host),  # length of hostname
                        host.encode('utf-8'),
                        port)

        elif self._message_type == WCMLMessageType.DATA:
            data: bytes = self.data
            return pack(f'!BQQ{len(data)}s',
                        self._message_type,
                        self._from_id,
                        self._to_id,
                        data)

        elif self._message_type == WCMLMessageType.CONNECTION_LOST:
            return pack(f'!BQQ',
                        self._message_type,
                        self._from_id,
                        self._to_id)

        elif self._message_type == WCMLMessageType.WEBSOCKET_PROMOTION:
            return pack(f'!BQQ',
                        self._message_type,
                        self._from_id,
                        self._to_id)

        else:
            raise NotImplementedError(f'unknown message type: {self._message_type}')

    @classmethod
    def from_bytes(cls, bytes_msg) -> 'WCMLMessage':
        """
        concert a bytes object into WCMLMessage object
        """
        reader = BytesReader(bytes_msg)
        wcml_message_type, from_id, to_id = unpack('!BQQ', reader.read(1 + 8 + 8))

        if wcml_message_type in [WCMLMessageType.CONNECTION_REQUEST,
                                 WCMLMessageType.CONNECTION_MADE]:
            host_length, = reader.read(1)
            host = reader.read(host_length).decode('utf-8')
            port = unpack('!H', reader.read(2))[0]

            return WCMLMessage(message_type=wcml_message_type,
                               from_id=from_id,
                               to_id=to_id,
                               host=host,
                               port=port)

        elif wcml_message_type == WCMLMessageType.DATA:
            data = reader.read()
            return WCMLMessage(message_type=wcml_message_type,
                               from_id=from_id,
                               to_id=to_id,
                               data=data)

        elif wcml_message_type == WCMLMessageType.CONNECTION_LOST:
            return WCMLMessage(message_type=wcml_message_type,
                               from_id=from_id,
                               to_id=to_id)

        elif wcml_message_type == WCMLMessageType.WEBSOCKET_PROMOTION:
            return WCMLMessage(message_type=wcml_message_type,
                               from_id=from_id,
                               to_id=to_id)

        else:
            raise NotImplementedError(f'unknown message type: {wcml_message_type}')

    def __getattr__(self, name):
        if self._message_type in [WCMLMessageType.CONNECTION_REQUEST,
                                  WCMLMessageType.CONNECTION_MADE] and name in ['host', 'port']:
            return self._attr[name]

        elif self._message_type == WCMLMessageType.DATA and name == 'data':
            return self._attr[name]

        elif self._message_type == WCMLMessageType.WEBSOCKET_PROMOTION and name == 'promotion_index':
            return self._attr[name]

        raise AttributeError(f'message does not have attribute: {name}')


class WCMLAuthenticator(object):
    """

    """

    def __init__(self):
        self._token_set = set()
        self._valid_time_length = 5  # seconds # TODO

    def authenticate(self, secret: bytes):
        train_name_bytes, timestamp, token_bytes = unpack('!9sd16s', secret)

        # check train name
        train_name = train_name_bytes.decode('utf-8')
        if train_name != 'NightMail':
            return False

        # check time stamp
        current_timestamp = time.time()
        if abs(timestamp - current_timestamp) > self._valid_time_length:
            return False

        # check uuid token
        token = uuid.UUID(bytes=token_bytes)
        if token in self._token_set:
            return False

        # add token to set
        self._token_set.add(token)

        # schedule token removal in (self._valid_time_length * 1.5) second(s)
        async def _discard_token():
            await asyncio.sleep(self._valid_time_length * 1.5)
            self._token_set.discard(token)

        asyncio.create_task(_discard_token())

        return True


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


class UniqueIDFactory(object):
    """

    """

    def __init__(self):
        self._next_id = 1

    def generate_id(self):
        ans = self._next_id
        self._next_id += 1
        return ans


def init_logger(log_level=logging.INFO):
    logger = colorlog.getLogger('skimbleshanks')
    assert len(logger.handlers) == 0
    logger.setLevel(log_level)

    if log_level == logging.DEBUG:
        log_format = ('{white}[{asctime}]'
                      '{blue}[{module}:{funcName}():{lineno}]'
                      '{log_color}[{levelname}] {message_log_color}{message}{reset}')
    else:
        log_format = ('{white}[{asctime}]'
                      '{log_color}[{levelname}] {message_log_color}{message}{reset}')
    colored_formatter = colorlog.ColoredFormatter(
        fmt=log_format,
        style='{',
        log_colors={
            'DEBUG': 'bold_cyan',
            'INFO': 'bold_green',
            'WARNING': 'bold_yellow',
            'ERROR': 'bold_red',
            'CRITICAL': 'bold_red,bg_white',
        },
        secondary_log_colors={
            'message': {
                'DEBUG': 'white',
                'INFO': 'white',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'bold_red,bg_white',
            }
        }
    )

    stream_handler = colorlog.StreamHandler()
    stream_handler.setFormatter(colored_formatter)
    stream_handler.setLevel(log_level)

    logger.addHandler(stream_handler)
