
class Station(object):
    def __init__(self):
        self._id_2_protocol = {}  # TODO weakref

    def register(self, protocol):
        self._id_2_protocol[protocol.id] = protocol

    def unregister(self, protocol):
        self._id_2_protocol.pop(protocol.id)

    # def get_protocol_by_id(self, protocol_id):
    #     return self._id_2_protocol[protocol_id]

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
