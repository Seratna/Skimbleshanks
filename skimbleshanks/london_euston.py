from asyncio.protocols import BaseProtocol
from typing import Dict, List, Set, Any, Union
import logging
import asyncio
from struct import pack, unpack
import socket
import time

import websockets
from websockets.client import WebSocketClientProtocol

from .station import Station
from .protocol import BaseProtocol
from .util import WCMLMessageType, BytesReader, FernetEncryptor, WCMLMessage, UniqueIDFactory

logger = logging.getLogger()


class LondonEuston(Station):
    """

    """

    def __init__(self,
                 london_euston_host,
                 london_euston_port,
                 glasgow_central_host,
                 glasgow_central_port,
                 password):
        Station.__init__(self)

        self._london_euston_host: str = london_euston_host
        self._london_euston_port: int = london_euston_port
        self._password = password

        self._wcml_client: WCMLClient = WCMLClient(station=self,
                                                   wcml_server_host=glasgow_central_host,
                                                   wcml_server_port=glasgow_central_port,
                                                   password=password)

    def register(self, protocol: 'LondonEustonProtocol'):
        self._id_2_protocol: Dict[int, LondonEustonProtocol]
        self._id_2_protocol[protocol.id] = protocol

    def unregister(self, protocol: 'LondonEustonProtocol'):
        self._id_2_protocol.pop(protocol.id, None)

    def outgoing_wcml_message(self, message: WCMLMessage):
        asyncio.create_task(self._wcml_client.send_message(message))

    def incoming_wcml_message(self, message: WCMLMessage):
        if message.message_type == WCMLMessageType.CONNECTION_MADE:
            try:
                protocol: LondonEustonProtocol = self._id_2_protocol[message.to_id]
            except KeyError:
                self.outgoing_wcml_message(WCMLMessage(message_type=WCMLMessageType.CONNECTION_LOST,
                                                       from_id=message.to_id,
                                                       to_id=message.from_id))
            else:
                protocol.set_counter_party_id(message.from_id)
                protocol.glasgow_central_connection_made(message.host, message.port)

        elif message.message_type == WCMLMessageType.DATA:
            try:
                protocol: LondonEustonProtocol = self._id_2_protocol[message.to_id]
            except KeyError:
                self.outgoing_wcml_message(WCMLMessage(message_type=WCMLMessageType.CONNECTION_LOST,
                                                       from_id=message.to_id,
                                                       to_id=message.from_id))
            else:
                protocol.data_received_from_wcml_counter_party(message.data)

        elif message.message_type == WCMLMessageType.CONNECTION_LOST:
            try:
                protocol: LondonEustonProtocol = self._id_2_protocol[message.to_id]
            except KeyError:
                pass
            else:
                protocol.glasgow_central_connection_lost()

        else:
            raise NotImplementedError

    async def start_service(self):
        await asyncio.gather(self._wcml_client.start_service(10),  # TODO config
                             self._london_euston_station_routine())

    async def _london_euston_station_routine(self):
        """
        this is the major working loop of the domestic server (London Euston)
        """
        loop = asyncio.get_running_loop()

        server = await loop.create_server(lambda: LondonEustonProtocol(station=self),
                                          host=self._london_euston_host,
                                          port=self._london_euston_port)
        async with server:
            logger.info(f'server running on {self._london_euston_host}:{self._london_euston_port}')
            await server.serve_forever()


class LondonEustonProtocol(BaseProtocol):
    """
    this is the Protocol that handles connection between
    the client device and the domestic server (London Euston)
    """

    NEGOTIATION = 0
    AUTHENTICATION = 1
    REQUEST = 2
    DATA = 3

    def __init__(self, station: 'LondonEuston'):
        BaseProtocol.__init__(self, station=station, counter_party_id=0)
        self._state = None

    def connection_made(self, transport):
        self._transport = transport
        self._station.register(protocol=self)
        self._state = self.NEGOTIATION

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

    def data_received(self, data: bytes):
        reader = BytesReader(data)

        if self._state == self.NEGOTIATION:
            version, = reader.read(1)
            if version != 0x05:
                self.close_transport_and_unregister()
                return

            n_methods, = reader.read(1)
            methods = reader.read(n_methods)
            if 0x02 not in methods:
                self.close_transport_and_unregister()
                return

            self._transport.write(b'\x05\x02')  # username / password
            self._state = self.AUTHENTICATION

        elif self._state == self.AUTHENTICATION:
            version, = reader.read(1)
            if version != 0x01:  # not a Socks5 connection
                self.close_transport_and_unregister()
                return

            username_length, = reader.read(1)
            username_bytes = reader.read(username_length)
            password_length, = reader.read(1)
            password_bytes = reader.read(password_length)

            username = username_bytes.decode('utf-8')
            password = password_bytes.decode('utf-8')

            # TODO authentication
            pass

            self._transport.write(b'\x01\x00')  # successful authorization
            self._state = self.REQUEST

        elif self._state == self.REQUEST:
            version, cmd, _, address_type = reader.read(4)
            if version != 0x05 or cmd != 0x01:
                self.close_transport_and_unregister()
                return

            if address_type == 0x03:  # domain
                # If a client sends a domain name, it should be resolved by the DNS on the server side
                host_length, = reader.read(1)
                host_bytes = reader.read(host_length)
                host = host_bytes.decode('utf-8')

            elif address_type == 0x01:  # ipv4
                host_bytes = reader.read(4)
                host = socket.inet_ntop(socket.AF_INET, host_bytes)

            elif address_type == 0x04:  # ipv6
                host_bytes = reader.read(16)
                host = socket.inet_ntop(socket.AF_INET6, host_bytes)

            else:
                self.close_transport_and_unregister()
                return

            port = unpack('!H', reader.read(2))[0]

            logger.info(f'request connection to {host}:{port}. '
                        f'alive protocols: {len(self._station._id_2_protocol)}')

            # request_glasgow_central_connection
            message = WCMLMessage(message_type=WCMLMessageType.CONNECTION_REQUEST,
                                  from_id=self.id,
                                  to_id=self._counter_party_id,
                                  host=host,
                                  port=port)
            self._station.outgoing_wcml_message(message)

        elif self._state == self.DATA:
            message = WCMLMessage(message_type=WCMLMessageType.DATA,
                                  from_id=self.id,
                                  to_id=self._counter_party_id,
                                  data=data)
            self._station.outgoing_wcml_message(message)

    def glasgow_central_connection_made(self, host, port):
        try:
            address_type = 0x01  # IPV4
            host = socket.inet_pton(socket.AF_INET, host)
        except OSError:
            address_type = 0x04  # IPV6
            host = socket.inet_pton(socket.AF_INET6, host)

        self._transport.write(pack(f'!BBBB{len(host)}sH', 0x05, 0x00, 0x00, address_type, host, port))

        # update state
        self._state = self.DATA

    def glasgow_central_connection_lost(self):
        if self._state == self.REQUEST:
            self._transport.write(pack('!BBBBIH', 0x05, 0x04, 0x00, 0x01, 0x00, 0x00))
        elif self._state == self.DATA:
            self.close_transport_and_unregister()


class WCMLClient(object):
    """

    """

    def __init__(self,
                 station,
                 wcml_server_host,
                 wcml_server_port,
                 password):

        self._station = station
        self._wcml_server_host = wcml_server_host
        self._wcml_server_port = wcml_server_port
        self._password = password

        self._ws: WebSocketClientProtocol = None
        self._ws_connection_available_event: asyncio.Event = None  # must be created "inside the loop"
        self._ws_promotion_index: int = 0
        self._ws_promotion_index_factory = UniqueIDFactory()

        self._ws_set: Set[WebSocketClientProtocol] = set()
        self._ws_set_lock: asyncio.Lock = asyncio.Lock()

        self._fernet = FernetEncryptor(password)

    async def start_service(self, num_ws_connections: int):
        self._ws_connection_available_event: asyncio.Event = asyncio.Event()  # must be created "inside the loop"
        await asyncio.wait([self.ws_client_routine() for _ in range(num_ws_connections)])

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

    async def ws_client_routine(self):
        """
        this is the major working loop of 1 websocket client
        """
        while True:
            # make headers for authentication
            timestamp = int(time.time() * 1000)
            token = pack('!9sQ', b'NightMail', timestamp)
            headers = {'TOKEN': self._fernet.encrypt(token).hex()}

            uri = f'ws://{self._wcml_server_host}:{self._wcml_server_port}/WCML'
            ws_protocol: WebSocketClientProtocol = None

            try:
                ws_protocol = await websockets.connect(uri=uri,
                                                       ping_interval=1,
                                                       ping_timeout=5,
                                                       extra_headers=headers)
            except ConnectionRefusedError:
                logger.warning('[WS] connection call failed')
                ws_protocol = None

            else:
                logger.info(f'[WS {id(ws_protocol)}] started')

                async with self._ws_set_lock:
                    self._ws_set.add(ws_protocol)
                    logger.info(f'[WS {id(ws_protocol)}] added to pool. pool size: {len(self._ws_set)}')

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
                if ws_protocol:
                    if self._ws is ws_protocol:
                        self._ws = None
                        self._ws_connection_available_event.clear()

                    await ws_protocol.close()

                    async with self._ws_set_lock:
                        self._ws_set.discard(ws_protocol)

                    await self._promote()

            # TODO re-connection (add waiting time before trying to re-connect)
            waiting_time = 0.1
            logger.info(f'[WS] trying to reconnect in {waiting_time} second(s)')
            await asyncio.sleep(waiting_time)
