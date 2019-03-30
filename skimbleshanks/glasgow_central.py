from typing import List, Dict
import logging
import json
import asyncio
from struct import pack, unpack

import aiohttp
from aiohttp import web

from .station import Station
from .protocol import BaseProtocol
from .util import WCMLMessageType, BytesReader

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger()


class GlasgowCentral(Station):
    """

    """

    def __init__(self):
        Station.__init__(self)
        self.wcml_server = WCMLServer(station=self, host='127.0.0.1', port=9999)

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

            logger.info(f'request connection to {host}:{port}')

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
                                                             to_id=from_id,
                                                             host=host,
                                                             port=port)

            asyncio.create_task(connect())

        elif message_type == WCMLMessageType.DATA:
            data = kwargs['data']

            protocol: GlasgowCentralProtocol = self._id_2_protocol[to_id]
            protocol.data_received_from_wcml_counter_party(data)

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

    @property
    def id(self):
        return id(self)

    def connection_made(self, transport):
        self._transport = transport
        self._station.register(protocol=self)

        host, port = transport.get_extra_info('peername')
        self._station.outgoing_wcml_message(message_type=WCMLMessageType.CONNECTION_MADE,
                                            from_id=self.id,
                                            to_id=self._counter_party_id,
                                            host=host,
                                            port=port)

    def connection_lost(self, exc):
        self._transport.close()
        # self._station.unregister(protocol=self)

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

    def __init__(self, station, host, port):
        self._station = station

        self._host = host
        self._port = port

        self._app = web.Application()
        self._app.router.add_get('/WCML', self._websocket_handler)

        self._ws: aiohttp.web.WebSocketResponse = None

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
            host: str = kwargs['host']
            port: int = kwargs['port']

            message = pack(f'!BQQB{len(host)}sH',
                           message_type,
                           from_id,
                           to_id,
                           len(host),  # length of hostname
                           host.encode('utf-8'),
                           port)

        else:
            raise NotImplementedError

        await self._ws.send_bytes(message)

    async def _websocket_handler(self, request):
        """
        baisc code structure was adopted from official docs of aiohttp:
        https://docs.aiohttp.org/en/stable/web_quickstart.html#websockets

        Args:
            request:
        """
        ws = self._ws = web.WebSocketResponse(heartbeat=1)  # send heartbeat to client every second
        await ws.prepare(request)

        async for msg in ws:  # type: aiohttp.http.WSMessage
            if msg.type == aiohttp.WSMsgType.TEXT:
                raise NotImplementedError

            elif msg.type == aiohttp.WSMsgType.BINARY:
                reader = BytesReader(msg.data)
                wcml_msg_type, from_id, to_id = unpack('!BQQ', reader.read(1 + 8 + 8))

                if wcml_msg_type == WCMLMessageType.CONNECTION_REQUEST:
                    host_length, = reader.read(1)
                    host = reader.read(host_length).decode('utf-8')
                    port = unpack('!H', reader.read(2))[0]

                    self._station.incoming_wcml_message(message_type=WCMLMessageType.CONNECTION_REQUEST,
                                                        from_id=from_id,
                                                        to_id=to_id,
                                                        host=host,
                                                        port=port)

                elif wcml_msg_type == WCMLMessageType.DATA:
                    data = reader.read()

                    self._station.incoming_wcml_message(message_type=WCMLMessageType.DATA,
                                                        from_id=from_id,
                                                        to_id=to_id,
                                                        data=data)

                else:
                    raise NotImplementedError

            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.warning(f'ws connection closed with exception {ws.exception()}')

            else:
                raise TypeError(f'unknown message type: {msg.type}')

        logger.info('websocket connection closed')

        self._ws = None
        return ws

    def start_service(self):
        web.run_app(self._app, host=self._host, port=self._port)
