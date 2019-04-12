from typing import List, Dict, Set
import logging
import asyncio
from struct import pack, unpack
import time

import websockets
from websockets.server import WebSocketServerProtocol

from .station import Station
from .protocol import BaseProtocol
from .util import WCMLMessageType, BytesReader, FernetEncryptor, WCMLMessage, UniqueIDFactory

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

    def outgoing_wcml_message(self, message: WCMLMessage):
        asyncio.create_task(self.wcml_server.send_message(message=message))

    def incoming_wcml_message(self, message: WCMLMessage):
        if message.message_type == WCMLMessageType.CONNECTION_REQUEST:
            logger.info(f'received request: {message.host}:{message.port}. '
                        f'alive protocols: {len(self._id_2_protocol)}')

            def protocol_factory() -> GlasgowCentralProtocol:
                return GlasgowCentralProtocol(station=self,
                                              counter_party_id=message.from_id)

            async def connect():
                loop = asyncio.get_event_loop()
                try:
                    await loop.create_connection(protocol_factory=protocol_factory,
                                                 host=message.host,
                                                 port=message.port)
                except (TimeoutError, OSError):
                    logger.info(f'failed to connect to {message.host}:{message.port}')
                    await self.wcml_server.send_message(WCMLMessage(message_type=WCMLMessageType.CONNECTION_LOST,
                                                                    from_id=0,
                                                                    to_id=message.from_id))

            asyncio.create_task(connect())

        elif message.message_type == WCMLMessageType.DATA:
            try:
                protocol: GlasgowCentralProtocol = self._id_2_protocol[message.to_id]
            except KeyError:
                self.outgoing_wcml_message(WCMLMessage(message_type=WCMLMessageType.CONNECTION_LOST,
                                                       from_id=message.to_id,
                                                       to_id=message.from_id))
            else:
                protocol.data_received_from_wcml_counter_party(message.data)

        elif message.message_type == WCMLMessageType.CONNECTION_LOST:
            try:
                protocol: GlasgowCentralProtocol = self._id_2_protocol[message.to_id]
            except KeyError:
                pass
            else:
                logger.info(f'close protocol {protocol.id} due to closed counter party protocol')
                protocol.close_transport_and_unregister()

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

        message = WCMLMessage(message_type=WCMLMessageType.CONNECTION_MADE,
                              from_id=self.id,
                              to_id=self._counter_party_id,
                              host=host,
                              port=port)
        self._station.outgoing_wcml_message(message)

    def connection_lost(self, exc):
        self._transport.close()
        message = WCMLMessage(message_type=WCMLMessageType.CONNECTION_LOST,
                              from_id=self.id,
                              to_id=self._counter_party_id)
        self._station.outgoing_wcml_message(message)

        logger.debug(f'protocol {self.id} lost connection')

    def close_transport_and_unregister(self):
        self._transport.close()
        self._station.unregister(protocol=self)

    def data_received(self, data):
        logger.debug(f'protocol {self.id} received data from destination')
        message = WCMLMessage(message_type=WCMLMessageType.DATA,
                              from_id=self.id,
                              to_id=self._counter_party_id,
                              data=data)
        self._station.outgoing_wcml_message(message)


class WCMLServer(object):
    """

    """

    def __init__(self, station, host, port, password):
        self._station = station
        self._host = host
        self._port = port
        self._password = password

        self._ws: WebSocketServerProtocol = None
        self._ws_connection_available_event: asyncio.Event = asyncio.Event()
        self._ws_promotion_index: int = 0
        self._ws_promotion_index_factory = UniqueIDFactory()

        self._ws_set: Set[WebSocketServerProtocol] = set()
        self._ws_set_lock: asyncio.Lock = asyncio.Lock()

        self._used_timestamp = int(time.time() * 1000)

        self._fernet = FernetEncryptor(password)

    def start_service(self):
        # TODO
        # On Python ≥ 3.5, serve() can also be used as an asynchronous context manager.
        # try use this feature later
        start_server = websockets.serve(self.websocket_handler, self._host, self._port)

        asyncio.get_event_loop().run_until_complete(start_server)
        asyncio.get_event_loop().run_forever()

    async def send_message(self, message: WCMLMessage):
        """
        send a websocket message to WCML peer
        """
        # wait for the ws connection to be available
        await self._ws_connection_available_event.wait()

        encrypted_message = self._fernet.encrypt(message.bytes())
        await self._ws.send(encrypted_message)  # TODO catch exceptions raised during sending

    async def _promote(self):
        promotion_index = self._ws_promotion_index_factory.generate_id()
        message = WCMLMessage(message_type=WCMLMessageType.WEBSOCKET_PROMOTION,
                              from_id=0,
                              to_id=0,
                              promotion_index=promotion_index)
        encrypted_message = self._fernet.encrypt(message.bytes())

        async with self._ws_set_lock:
            for ws_protocol in self._ws_set:
                if ws_protocol.closed:
                    continue
                try:
                    await ws_protocol.send(encrypted_message)
                except websockets.exceptions.ConnectionClosed:
                    pass

    async def websocket_handler(self, ws_protocol, request_uri):
        """
        see: https://websockets.readthedocs.io/en/stable/intro.html#basic-example
        """
        logger.info(f'[WS {id(ws_protocol)}] started')

        # ##############
        # authentication
        # ##############

        headers = ws_protocol.request_headers
        encrypted_token = bytes.fromhex(headers['TOKEN'])
        token = self._fernet.decrypt(encrypted_token)
        reader = BytesReader(token)

        train_name = reader.read(9).decode('utf-8')
        if train_name != 'NightMail':
            logger.info(f'[WS {id(ws_protocol)}] failed authentication (train name)')
            return

        timestamp = int.from_bytes(reader.read(8), 'big')
        if timestamp <= self._used_timestamp:
            logger.info(f'[WS {id(ws_protocol)}] failed authentication (timestamp)')
            return
        else:
            self._used_timestamp = timestamp

        # #######
        # service
        # #######

        async with self._ws_set_lock:
            self._ws_set.add(ws_protocol)
            logger.info(f'[WS {id(ws_protocol)}] added to pool. pool size: {len(self._ws_set)}')

        try:
            # send promotion message
            promotion_index = self._ws_promotion_index_factory.generate_id()
            message = WCMLMessage(message_type=WCMLMessageType.WEBSOCKET_PROMOTION,
                                  from_id=0,
                                  to_id=0,
                                  promotion_index=promotion_index)
            encrypted_message = self._fernet.encrypt(message.bytes())
            await ws_protocol.send(encrypted_message)  # TODO catch exceptions raised during sending

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
                    encrypted_message: bytes = await ws_protocol.recv()
                except websockets.exceptions.ConnectionClosed as e:
                    logger.warning(e)
                    break
                else:
                    decrypted_message = self._fernet.decrypt(encrypted_message)
                    message = WCMLMessage.from_bytes(decrypted_message)

                    if message.message_type == WCMLMessageType.WEBSOCKET_PROMOTION:
                        if message.promotion_index > self._ws_promotion_index:
                            logger.info(f'[WS {id(ws_protocol)}] promoted')
                            self._ws = ws_protocol
                            self._ws_promotion_index = message.promotion_index
                            self._ws_connection_available_event.set()
                    else:
                        self._station.incoming_wcml_message(message=message)

            logger.warning(f'[WS {id(ws_protocol)}] closed')

        finally:  # clean-up actions
            if self._ws is ws_protocol:
                self._ws = None
                self._ws_connection_available_event.clear()

            async with self._ws_set_lock:
                self._ws_set.discard(ws_protocol)

            await self._promote()
