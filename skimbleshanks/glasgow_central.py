from typing import List, Dict
import logging
import asyncio
from struct import pack, unpack
import time

import aiohttp
from aiohttp import web

from .station import Station
from .protocol import BaseProtocol
from .util import WCMLMessageType, BytesReader, FernetEncryptor

logging.basicConfig(level=logging.DEBUG)
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

            logger.info(f'request connection to {host}:{port}. alive protocols: {len(self._id_2_protocol)}')

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
                protocol.close()

        else:
            raise NotImplementedError

    def on_wcml_close(self):
        """
        websocket connection was closed. clean up the protocols.
        """
        protocols: List[GlasgowCentralProtocol] = list(self._id_2_protocol.values())
        for protocol in protocols:
            protocol.close()
        logger.debug('all protocols closed')

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

    @property
    def id(self):
        return id(self)

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

        self._station.outgoing_wcml_message(message_type=WCMLMessageType.CONNECTION_MADE,
                                            from_id=self.id,
                                            to_id=self._counter_party_id,
                                            host=host,
                                            port=port)

    def connection_lost(self, exc):
        self._transport.close()
        # self._station.unregister(protocol=self)

    def close(self):
        self._transport.close()
        self._station.unregister(protocol=self)

    def data_received(self, data):
        self._station.outgoing_wcml_message(message_type=WCMLMessageType.DATA,
                                            from_id=self.id,
                                            to_id=self._counter_party_id,
                                            data=data)

    def data_received_from_wcml_counter_party(self, data: bytes):
        self._transport.write(data)


class WCMLServer(object):
    """

    """

    def __init__(self, station, host, port, password):
        self._station = station

        self._host = host
        self._port = port
        self._password = password

        self._app = web.Application()
        self._app.router.add_get('/WCML', self._websocket_handler)

        self._ws: aiohttp.web.WebSocketResponse = None

        self._fernet = FernetEncryptor(self._password)

        self._used_timestamp = int(time.time() * 1000)

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

        # because of https://bugs.python.org/issue29930
        # BaseProtocol._drain_helper() raise AssertionError sometimes.
        # see https://github.com/aio-libs/aiohttp/issues/2934
        # and https://github.com/aaugustin/websockets/commit/198b71537917adb44002573b14cbe23dbd4c21a2
        # for more details
        encrypted_message = self._fernet.encrypt(message)

        while True:
            try:
                await self._ws.send_bytes(encrypted_message)
            except AssertionError:
                logger.warning('BaseProtocol._drain_helper() AssertionError caught')
            else:
                break


    async def _websocket_handler(self, request):
        """
        baisc code structure was adopted from official docs of aiohttp:
        https://docs.aiohttp.org/en/stable/web_quickstart.html#websockets

        Args:
            request:
        """
        # authentication
        headers = request.headers
        encrypted_token = bytes.fromhex(headers['TOKEN'])
        token = self._fernet.decrypt(encrypted_token)
        reader = BytesReader(token)

        train_name = reader.read(9).decode('utf-8')
        if train_name != 'NightMail':
            return web.Response(text="bad request")

        timestamp = int.from_bytes(reader.read(8), 'big')
        if timestamp <= self._used_timestamp:
            return web.Response(text="bad request")
        else:
            self._used_timestamp = timestamp

        # websocket connection
        ws = self._ws = web.WebSocketResponse(heartbeat=1)  # send heartbeat to client every second
        await ws.prepare(request)

        async for msg in ws:  # type: aiohttp.http.WSMessage
            if msg.type == aiohttp.WSMsgType.TEXT:
                raise NotImplementedError

            elif msg.type == aiohttp.WSMsgType.BINARY:
                decrypted_message = self._fernet.decrypt(msg.data)
                reader = BytesReader(decrypted_message)
                wcml_msg_type, from_id, to_id = unpack('!BQQ', reader.read(1 + 8 + 8))

                if wcml_msg_type == WCMLMessageType.CONNECTION_REQUEST:
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

            else:
                raise TypeError(f'unknown message type: {msg.type}')

        logger.info('websocket connection closed')

        # close all protocols
        self._station.on_wcml_close()

        self._ws = None
        return ws

    def start_service(self):
        web.run_app(self._app, host=self._host, port=self._port)
