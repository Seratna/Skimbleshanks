import logging
import asyncio
from struct import pack, unpack
import socket
import json

import aiohttp
from aiohttp import web


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger()


class LondonEuston(object):
    """

    """
    def __init__(self):
        self._london_euston_host = '192.168.10.51'
        self._london_euston_port = 8888

        self._glasgow_central_host = '127.0.0.1'
        self._glasgow_central_port = '9999'

        self._ws: aiohttp.web.WebSocketResponse = None

    async def _west_coast_main_line_routine(self):
        """
        this is the major working loop of the websocket (west coast main line)
        """
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url=f'ws://{self._glasgow_central_host}:{self._glasgow_central_port}/WCML',
                                          heartbeat=5) as ws:
                self._ws = ws

                # TODO authentication
                pass

                # wait for messages
                async for msg in ws:  # type: aiohttp.http.WSMessage
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        if msg.data == 'close cmd':
                            await ws.close()
                            break
                        else:
                            await ws.send_str(msg.data + '/answer')
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        break

            self._ws = None

    async def _night_mail_routine(self):
        """
        this is the major working loop of the domestic server (night mail)
        """
        server = await asyncio.start_server(client_connected_cb=self._handle_connection,
                                            host=self._london_euston_host,
                                            port=self._london_euston_port)
        async with server:
            await server.serve_forever()

    async def _send(self, message):
        """

        """
        await self._ws.send_str(message)

    async def _handle_connection(self,
                                 reader: asyncio.StreamReader,
                                 writer: asyncio.StreamWriter):

        # ###########
        # negotiation
        # ###########

        version, = await reader.read(1)
        if version != 0x05:  # not a Socks5 connection
            writer.close()
            return

        n_methods, = await reader.read(1)
        *methods, = await reader.read(n_methods)
        if 0x02 not in methods:
            writer.close()
            return

        writer.write(b'\x05\x02')
        await writer.drain()

        # ##############
        # authentication
        # ##############

        version, = await reader.read(1)
        if version != 0x01:  # not a Socks5 connection
            writer.close()
            return

        username_length, = await reader.read(1)
        username_bytes = await reader.read(username_length)
        password_length, = await reader.read(1)
        password_bytes = await reader.read(password_length)

        username = username_bytes.decode('utf-8')
        password = password_bytes.decode('utf-8')

        # TODO authentication
        pass

        writer.write(b'\x01\x00')  # successful authorization
        await writer.drain()

        # #######
        # request
        # #######

        version, cmd, _, address_type = await reader.read(4)
        if version != 0x05 or cmd != 0x01:
            writer.close()
            return

        if address_type == 0x03:  # domain
            # If a client sends a domain name, it should be resolved by the DNS on the server side
            hostname_length, = await reader.read(1)
            hostname_bytes, = await reader.read(hostname_length)
            hostname = hostname_bytes.decode('utf-8')

        elif address_type == 0x01:  # ipv4
            hostname_bytes = await reader.read(4)
            hostname = socket.inet_ntop(socket.AF_INET, hostname_bytes)

        elif address_type == 0x04:  # ipv6
            hostname_bytes = await reader.read(16)
            hostname = socket.inet_ntop(socket.AF_INET6, hostname_bytes)

        else:
            writer.close()
            return

        port = unpack('!H', await reader.read(2))[0]

        # #########################
        # connection to destination
        # #########################

        # abort if no websocket connection available
        if self._ws is None or self._ws.closed:
            logger.warning(f'websocket connection not available, abort {hostname}:{port}')

            writer.write(pack('!BB', 0x05, 0x04))  # '04' Host unreachable
            await writer.drain()

            writer.close()
            return

        # 
        await self._send(json.dumps({'host': hostname, 'port': port}))

    async def start_service(self):
        await asyncio.gather(self._west_coast_main_line_routine(),
                             self._night_mail_routine())


async def main():
    london_euston = LondonEuston()
    await london_euston.start_service()


if __name__ == '__main__':
    asyncio.run(main())
