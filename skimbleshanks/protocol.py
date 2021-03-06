import logging
import asyncio

from.station import Station


logger = logging.getLogger()


class BaseProtocol(asyncio.Protocol):
    """
    this is the base protocol for both London Euston and Glasgow Central
    """

    def __init__(self,
                 station: Station,
                 counter_party_id: int):

        self._station = station
        self._unique_id = station.generate_id()
        self._counter_party_id = counter_party_id

        self._transport = None

    @property
    def id(self):
        return self._unique_id

    def set_counter_party_id(self, counter_party_id: int):
        self._counter_party_id = counter_party_id

    def connection_made(self, transport):
        raise NotImplementedError

    def connection_lost(self, exc):
        raise NotImplementedError

    def data_received(self, data):
        raise NotImplementedError

    def data_received_from_wcml_counter_party(self, data: bytes):
        logger.debug(f'protocol {self.id} received data from counter party')
        self._transport.write(data)
