from typing import List, Dict
import logging
import asyncio
from struct import pack, unpack
import time

import websockets

from .station import Station
from .protocol import BaseProtocol
from .util import WCMLMessageType, BytesReader, FernetEncryptor


logger = logging.getLogger()


class GlasgowCentral(Station):
    """

    """

    def __init__(self,
                 glasgow_central_host,
                 glasgow_central_port,
                 password):
        Station.__init__(self)
        self._glasgow_central_host = glasgow_central_host
        self._glasgow_central_port = glasgow_central_port
        self._password = password

        self.wcml_server = WCMLServer(station=self,
                                      host=glasgow_central_host,
                                      port=glasgow_central_port,
                                      password=password)

    def outgoing_wcml_message(self, *,
                              message_type,
                              from_id,
                              to_id,
                              **kwargs):
        asyncio.create_task(self.wcml_server.send_wcml_message(message_type=message_type,
                                                               from_id=from_id,
                                                               to_id=to_id,
                                                               **kwargs))

    def incoming_wcml_message(self, *,
                              message_type,
                              from_id,
                              to_id,
                              **kwargs):
        if message_type == WCMLMessageType.CONNECTION_REQUEST:
            host = kwargs['host']
            port = kwargs['port']

            logger.info(f'received request: {host}:{port}. alive protocols: {len(self._id_2_protocol)}')

            def protocol_factory() -> GlasgowCentralProtocol:
                return GlasgowCentralProtocol(station=self,
                                              counter_party_id=from_id)

            async def connect():
                loop = asyncio.get_running_loop()
                try:
                    await loop.create_connection(protocol_factory=protocol_factory,
                                                 host=host,
                                                 port=port)
                except (TimeoutError, OSError):
                    logger.info(f'failed to connect to {host}:{port}')
                    await self.wcml_server.send_wcml_message(message_type=WCMLMessageType.CONNECTION_LOST,
                                                             from_id=0,
                                                             to_id=from_id)

            asyncio.create_task(connect())

        elif message_type == WCMLMessageType.DATA:
            data = kwargs['data']

            try:
                protocol: GlasgowCentralProtocol = self._id_2_protocol[to_id]
            except KeyError:
                self.outgoing_wcml_message(message_type=WCMLMessageType.CONNECTION_LOST,
                                           from_id=to_id,
                                           to_id=from_id)
            else:
                protocol.data_received_from_wcml_counter_party(data)

        elif message_type == WCMLMessageType.CONNECTION_LOST:
            try:
                protocol: GlasgowCentralProtocol = self._id_2_protocol[to_id]
            except KeyError:
                pass
            else:
                logger.info(f'close protocol {protocol.id} due to closed counter party protocol')
                protocol.close()

        else:
            raise NotImplementedError

    def start_service(self):
        raise NotImplementedError


class GlasgowCentralProtocol(BaseProtocol):
    """
    this is the Protocol that handles connection between
    the over-sea server (Glasgow Central) and the destination service
    """

    def __init__(self,
                 station: GlasgowCentral,
                 counter_party_id: int):
        BaseProtocol.__init__(self, station=station, counter_party_id=counter_party_id)
        logger.debug(f'protocol {self.id} created')

    def connection_made(self, transport):
        self._transport = transport
        self._station.register(protocol=self)

        peername = transport.get_extra_info('peername')
        if len(peername) == 2:
            host, port = peername
        elif len(peername) == 4:
            host, port, flow_info, scope_id = peername
        else:
            raise ValueError(f'unknown peername format: {peername}')

        logger.debug(f'protocol {self.id} made connection to {host}:{port}')

        self._station.outgoing_wcml_message(message_type=WCMLMessageType.CONNECTION_MADE,
                                            from_id=self.id,
                                            to_id=self._counter_party_id,
                                            host=host,
                                            port=port)

    def connection_lost(self, exc):
        self._transport.close()
        # self._station.unregister(protocol=self)

        logger.debug(f'protocol {self.id} lost connection')

    def close(self):
        self._transport.close()
        self._station.unregister(protocol=self)

    def data_received(self, data):
        logger.debug(f'protocol {self.id} received data from destination')
        self._station.outgoing_wcml_message(message_type=WCMLMessageType.DATA,
                                            from_id=self.id,
                                            to_id=self._counter_party_id,
                                            data=data)

    def data_received_from_wcml_counter_party(self, data: bytes):
        logger.debug(f'protocol {self.id} received data from counter party')
        self._transport.write(data)


class WCMLServer(object):
    """

    """
    def __init__(self, station, host, port, password):
        self._station = station
        self._host = host
        self._port = port
        self._password = password

        self._ws = None
        self._ws_connection_available_event = asyncio.Event()

        self._fernet = FernetEncryptor(password)

        self._used_timestamp = int(time.time() * 1000)

    async def websocket_handler(self, ws_protocol, request_uri):
        """
        see: https://websockets.readthedocs.io/en/stable/intro.html#basic-example
        """
        logger.info(f'websocket connection {id(ws_protocol)} started')

        # ##############
        # authentication
        # ##############

        headers = ws_protocol.request_headers
        encrypted_token = bytes.fromhex(headers['TOKEN'])
        token = self._fernet.decrypt(encrypted_token)
        reader = BytesReader(token)

        train_name = reader.read(9).decode('utf-8')
        if train_name != 'NightMail':
            return

        timestamp = int.from_bytes(reader.read(8), 'big')
        if timestamp <= self._used_timestamp:
            return
        else:
            self._used_timestamp = timestamp

        # #######
        # service
        # #######

        self._ws = ws_protocol
        self._ws_connection_available_event.set()

        # websockets library supports Asynchronous iterators like:
        #
        # async for message in websocket:
        #     ...
        #
        # but it is difficult to catch the exception raised during the iteration
        # so the "old" while loop style is used here
        # see: https://websockets.readthedocs.io/en/stable/intro.html#asynchronous-iterators
        while True:
            try:
                message: bytes = await ws_protocol.recv()
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(e)
                break
            else:
                decrypted_message = self._fernet.decrypt(message)
                reader = BytesReader(decrypted_message)
                wcml_message_type, from_id, to_id = unpack('!BQQ', reader.read(1 + 8 + 8))

                if wcml_message_type == WCMLMessageType.CONNECTION_REQUEST:
                    host_length, = reader.read(1)
                    host = reader.read(host_length).decode('utf-8')
                    port = unpack('!H', reader.read(2))[0]

                    self._station.incoming_wcml_message(message_type=wcml_message_type,
                                                        from_id=from_id,
                                                        to_id=to_id,
                                                        host=host,
                                                        port=port)

                elif wcml_message_type == WCMLMessageType.DATA:
                    data = reader.read()

                    self._station.incoming_wcml_message(message_type=wcml_message_type,
                                                        from_id=from_id,
                                                        to_id=to_id,
                                                        data=data)

                elif wcml_message_type == WCMLMessageType.CONNECTION_LOST:
                    self._station.incoming_wcml_message(message_type=wcml_message_type,
                                                        from_id=from_id,
                                                        to_id=to_id)
                else:
                    raise NotImplementedError(f'unknown message type: {message}')

        logger.warning(f'websocket connection {id(ws_protocol)} is closed')

        self._ws_connection_available_event.clear()
        self._ws = None

    async def send_wcml_message(self, *,
                                message_type,
                                from_id,
                                to_id,
                                **kwargs):
        """
        send a websocket message to WCML peer
        """
        # wait for the ws connection to be available
        await self._ws_connection_available_event.wait()

        # construct binary message according to message type
        if message_type == WCMLMessageType.CONNECTION_MADE:
            host: str = kwargs['host']
            port: int = kwargs['port']

            message = pack(f'!BQQB{len(host)}sH',
                           message_type,
                           from_id,
                           to_id,
                           len(host),  # length of hostname
                           host.encode('utf-8'),
                           port)

        elif message_type == WCMLMessageType.DATA:
            data = kwargs['data']

            message = pack(f'!BQQ{len(data)}s',
                           message_type,
                           from_id,
                           to_id,
                           data)

        elif message_type == WCMLMessageType.CONNECTION_LOST:
            message = pack(f'!BQQ',
                           message_type,
                           from_id,
                           to_id)

        else:
            raise NotImplementedError

        encrypted_message = self._fernet.encrypt(message)
        await self._ws.send(encrypted_message)

    def start_service(self):
        # TODO
        # On Python ≥ 3.5, serve() can also be used as an asynchronous context manager.
        # try use this feature later
        start_server = websockets.serve(self.websocket_handler, self._host, self._port)

        asyncio.get_event_loop().run_until_complete(start_server)
        asyncio.get_event_loop().run_forever()
