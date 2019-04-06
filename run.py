import argparse
import json
import asyncio
import logging

import uvloop

from skimbleshanks.glasgow_central import GlasgowCentral
from skimbleshanks.london_euston import LondonEuston


# TODO
# uvloop (https://github.com/MagicStack/uvloop) currently has a bug:
# The Server class was missing __aenter__ and __aexit__ magic methods to
# allow usage of the form:
#
# async with server:
#     await server.serve_forever()
# see https://github.com/MagicStack/uvloop/issues/221
# this issue was fixed in PR #224 (2019/02/17).
# use uvloop after a new release with these updates
# uvloop.install()


def run():
    parser = argparse.ArgumentParser(description='start Glasgow_Central (server) or London_Euston (client)')

    parser.add_argument('--glasgow_central', dest='station_name', action='store_const', const='glasgow_central')
    parser.add_argument('--london_euston', dest='station_name', action='store_const', const='london_euston')
    parser.set_defaults(station_name='')

    parser.add_argument('--config', required=True)

    parser.add_argument('--debug', dest='debug', action='store_true')
    parser.set_defaults(debug=False)

    args = parser.parse_args()

    # ########################

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    station_name = args.station_name
    if station_name == 'glasgow_central':
        with open(args.config) as file:
            config = json.load(file)

        glasgow_central = GlasgowCentral(glasgow_central_host=config['glasgow_central_host'],
                                         glasgow_central_port=config['glasgow_central_port'],
                                         password=config['password'])
        glasgow_central.wcml_server.start_service()

    elif station_name == 'london_euston':
        with open(args.config) as file:
            config = json.load(file)

        london_euston = LondonEuston(london_euston_host=config['london_euston_host'],
                                     london_euston_port=config['london_euston_port'],
                                     glasgow_central_host=config['glasgow_central_host'],
                                     glasgow_central_port=config['glasgow_central_port'],
                                     password=config['password'])

        asyncio.run(london_euston.start_service())

    else:
        raise ValueError('unknown station name')


def main():
    run()


if __name__ == '__main__':
    main()
