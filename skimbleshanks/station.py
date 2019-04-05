from weakref import WeakValueDictionary


class Station(object):
    def __init__(self):
        self._id_2_protocol = WeakValueDictionary()  # TODO weakref

    def register(self, protocol):
        self._id_2_protocol[protocol.id] = protocol

    def unregister(self, protocol):
        self._id_2_protocol.pop(protocol.id, None)

    def outgoing_wcml_message(self, *,
                              message_type,
                              from_id,
                              to_id,
                              **kwargs):
        raise NotImplementedError

    def incoming_wcml_message(self, *,
                              message_type,
                              from_id,
                              to_id,
                              **kwargs):
        raise NotImplementedError

    def start_service(self):
        raise NotImplementedError
