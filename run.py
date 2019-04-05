import argparse
import json
import asyncio

from skimbleshanks.glasgow_central import GlasgowCentral
from skimbleshanks.london_euston import LondonEuston


def run():
    parser = argparse.ArgumentParser(description='start Glasgow_Central (server) or London_Euston (client)')

    parser.add_argument('--glasgow_central', dest='station_name', action='store_const', const='glasgow_central')
    parser.add_argument('--london_euston', dest='station_name', action='store_const', const='london_euston')
    parser.set_defaults(station_name='')
    parser.add_argument('--config', required=True)

    args = parser.parse_args()

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
