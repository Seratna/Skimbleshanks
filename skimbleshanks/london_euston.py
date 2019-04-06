from typing import Dict, List
import logging
import asyncio
from struct import pack, unpack
import socket
import time

import aiohttp
from aiohttp import web

from .station import Station
from .protocol import BaseProtocol
from .util import WCMLMessageType, BytesReader, FernetEncryptor


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

    def outgoing_wcml_message(self, *,
                              message_type,
                              from_id,
                              to_id,
                              **kwargs):
        asyncio.create_task(self._wcml_client.send_wcml_message(message_type=message_type,
                                                                from_id=from_id,
                                                                to_id=to_id,
                                                                **kwargs))

    def incoming_wcml_message(self, *,
                              message_type,
                              from_id,
                              to_id,
                              **kwargs):
        if message_type == WCMLMessageType.CONNECTION_MADE:
            host = kwargs['host']
            port = kwargs['port']

            try:
                protocol: LondonEustonProtocol = self._id_2_protocol[to_id]
            except KeyError:
                self.outgoing_wcml_message(message_type=WCMLMessageType.CONNECTION_LOST,
                                           from_id=to_id,
                                           to_id=from_id)
            else:
                protocol.set_counter_party_id(from_id)
                protocol.glasgow_central_connection_made(host, port)

        elif message_type == WCMLMessageType.DATA:
            data = kwargs['data']

            try:
                protocol: LondonEustonProtocol = self._id_2_protocol[to_id]
            except KeyError:
                self.outgoing_wcml_message(message_type=WCMLMessageType.CONNECTION_LOST,
                                           from_id=to_id,
                                           to_id=from_id)
            else:
                protocol.data_received_from_wcml_counter_party(data)

        elif message_type == WCMLMessageType.CONNECTION_LOST:
            try:
                protocol: LondonEustonProtocol = self._id_2_protocol[to_id]
            except KeyError:
                pass
            else:
                protocol.glasgow_central_connection_lost()

        else:
            raise NotImplementedError

    def on_wcml_close(self):
        """
        websocket connection was closed. clean up the protocols.
        """
        protocols: List[LondonEustonProtocol] = list(self._id_2_protocol.values())
        for protocol in protocols:
            protocol.close()
        logger.info('all protocols closed')

    async def start_service(self):
        await asyncio.gather(self._wcml_client.wcml_client_routine(),
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
        # self._station.unregister(protocol=self)

    def close(self):
        self._transport.close()
        self._station.unregister(protocol=self)

    def data_received(self, data: bytes):
        reader = BytesReader(data)

        if self._state == self.NEGOTIATION:
            version, = reader.read(1)
            if version != 0x05:
                self.close()
                return

            n_methods, = reader.read(1)
            methods = reader.read(n_methods)
            if 0x02 not in methods:
                self.close()
                return

            self._transport.write(b'\x05\x02')  # username / password
            self._state = self.AUTHENTICATION

        elif self._state == self.AUTHENTICATION:
            version, = reader.read(1)
            if version != 0x01:  # not a Socks5 connection
                self.close()
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
                self.close()
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
                self.close()
                return

            port = unpack('!H', reader.read(2))[0]

            logger.info(f'request connection to {host}:{port}. '
                        f'alive protocols: {len(self._station._id_2_protocol)}')

            # request_glasgow_central_connection
            self._station.outgoing_wcml_message(message_type=WCMLMessageType.CONNECTION_REQUEST,
                                                from_id=self.id,
                                                to_id=self._counter_party_id,
                                                host=host,
                                                port=port)

        elif self._state == self.DATA:
            self._station.outgoing_wcml_message(message_type=WCMLMessageType.DATA,
                                                from_id=self.id,
                                                to_id=self._counter_party_id,
                                                data=data)

    def data_received_from_wcml_counter_party(self, data: bytes):
        self._transport.write(data)

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
            self.close()


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

        self._ws: aiohttp.web.WebSocketResponse = None

        self._fernet = FernetEncryptor(password)

    async def send_wcml_message(self, *,
                                message_type,
                                from_id,
                                to_id,
                                **kwargs):
        """
        send a websocket message to WCML peer
        """
        # abort if no websocket connection available
        if self._ws is None or self._ws.closed:
            logger.warning(f'websocket connection not available')
            return

        # construct binary message according to message type
        if message_type == WCMLMessageType.CONNECTION_REQUEST:
            host: str = kwargs['host']
            port: int = kwargs['port']

            message = pack(f'!BQQB{len(host)}sH',
                           message_type,
                           from_id,
                           to_id,  # to_id
                           len(host),  # length of hostname
                           host.encode('utf-8'),
                           port)

        elif message_type == WCMLMessageType.DATA:
            data: bytes = kwargs['data']

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
        await self._ws.send_bytes(encrypted_message)

    async def wcml_client_routine(self):
        """
        this is the major working loop of the websocket client
        """
        while True:
            async with aiohttp.ClientSession() as session:
                # make headers for authentication
                timestamp = int(time.time() * 1000)
                token = pack('!9sQ', b'NightMail', timestamp)
                headers = {'TOKEN': self._fernet.encrypt(token).hex()}

                # ws connection
                async with session.ws_connect(
                        url=f'ws://{self._wcml_server_host}:{self._wcml_server_port}/WCML',
                        autoping=True,  # automatically send pong on ping message from server
                        headers=headers
                ) as ws:
                    self._ws = ws

                    # wait for messages
                    async for msg in ws:  # type: aiohttp.http.WSMessage
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            raise NotImplementedError

                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            decrypted_message = self._fernet.decrypt(msg.data)
                            reader = BytesReader(decrypted_message)
                            wcml_msg_type, from_id, to_id = unpack('!BQQ', reader.read(1 + 8 + 8))

                            if wcml_msg_type == WCMLMessageType.CONNECTION_MADE:
                                host_length, = reader.read(1)
                                host = reader.read(host_length).decode('utf-8')
                                port = unpack('!H', reader.read(2))[0]

                                self._station.incoming_wcml_message(message_type=wcml_msg_type,
                                                                    from_id=from_id,
                                                                    to_id=to_id,
                                                                    host=host,
                                                                    port=port)

                            elif wcml_msg_type == WCMLMessageType.DATA:
                                data = reader.read()

                                self._station.incoming_wcml_message(message_type=wcml_msg_type,
                                                                    from_id=from_id,
                                                                    to_id=to_id,
                                                                    data=data)

                            elif wcml_msg_type == WCMLMessageType.CONNECTION_LOST:
                                self._station.incoming_wcml_message(message_type=wcml_msg_type,
                                                                    from_id=from_id,
                                                                    to_id=to_id)

                            else:
                                raise NotImplementedError

                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            logger.info(f'received aiohttp.WSMsgType.ERROR message: {msg.data}')

                logger.info('websocket connection closed')

                # close all protocols
                self._station.on_wcml_close()

                self._ws = None
