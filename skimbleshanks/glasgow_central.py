import logging

import aiohttp
from aiohttp import web


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger()


class GlasgowCentral(object):
    """

    """

    def __init__(self, host, port):
        self._host = host
        self._port = port

        self._app = web.Application()
        self._app.router.add_get('/WCML', self._websocket_handler)

    async def _websocket_handler(self, request):
        """
        baisc code structure was adopted from official docs of aiohttp:
        https://docs.aiohttp.org/en/stable/web_quickstart.html#websockets

        Args:
            request:
        """
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        async for msg in ws:  # type: aiohttp.http.WSMessage
            if msg.type == aiohttp.WSMsgType.TEXT:
                print(msg.data, msg.type)
                logger.info(f'received ws msg: {msg.data}')

            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.warning(f'ws connection closed with exception {ws.exception()}')

        logger.info('websocket connection closed')

        return ws

    def start(self):
        web.run_app(self._app, host=self._host, port=self._port)


def main():
    glasgow_central = GlasgowCentral(host='127.0.0.1', port=9999)
    glasgow_central.start()


if __name__ == '__main__':
    main()

