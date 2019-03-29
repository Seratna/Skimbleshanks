
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
