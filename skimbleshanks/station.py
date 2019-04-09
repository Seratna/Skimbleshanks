from weakref import WeakValueDictionary

from .util import UniqueIDFactory


class Station(object):
    def __init__(self):
        self._id_2_protocol = WeakValueDictionary()
        self._unique_id_factory = UniqueIDFactory()

    def generate_id(self):
        return self._unique_id_factory.generate_id()

    def register(self, protocol):
        self._id_2_protocol[protocol.id] = protocol

    def unregister(self, protocol):
        self._id_2_protocol.pop(protocol.id, None)

    def outgoing_wcml_message(self, message):
        raise NotImplementedError

    def incoming_wcml_message(self, message):
        raise NotImplementedError

    def start_service(self):
        raise NotImplementedError
