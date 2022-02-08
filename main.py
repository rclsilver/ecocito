#!/usr/bin/env python

import json
import logging
import os
import requests
import time as t

from bs4 import BeautifulSoup as bs
from datetime import datetime
from dateutil.relativedelta import relativedelta
from paho.mqtt import client as mqtt
from pathlib import Path
from pytz import timezone
from typing import Dict, Optional, Union


class EcocitoException(Exception):
    pass


class Ecocito:
    def __init__(self, subdomain: str, username: str, password: str):
        self._base_url = f'https://{subdomain}.ecocito.com'
        self._username = username
        self._password = password
        self._session = requests.Session()

    def _request(self, method: str, uri: str, params: Optional[Dict] = None, data: Optional[Dict] = None, headers: Optional[Dict] = None) -> requests.Response:
        return self._session.request(method, f'{self._base_url}{uri}', data=data, params=params, headers=headers)

    def login(self) -> None:
        r = self._request('POST', '/Usager/Profil/Connexion', data={
            'Identifiant': self._username,
            'MotDePasse': self._password,
            'MaintenirConnexion': False,
            'FranceConnectActif': False,
        })

        if r.status_code != 200:
            raise EcocitoException(f'Invalid HTTP response: {r.status_code}')

        html = bs(r.content, 'html.parser')
        error = html.find_all('div', { 'class': 'validation-summary-errors' })

        if error:
            raise EcocitoException(error[0].find('li').text)

        logging.debug('Connected as %s', self._username)

    def logout(self) -> None:
        r = self._request('GET', '/Usager/Profil/Deconnexion')

        if r.status_code != 200:
            raise EcocitoException(f'Invalid HTTP response: {r.status_code}')

        logging.debug('Session closed')

    def get_levees(self, date_start: datetime, date_end: datetime) -> dict:
        logging.debug('Fetching data between %s and %s', date_start, date_end)

        r = self._request(
            'GET',
            '/Usager/Collecte/GetCollecte',
            params={
                'charger': 'true',
                'idMatiere': -1,
                'sort': '[{"selector":"DATE_DONNEE","desc":false}]',
                'skip': 0,
                'take': 20,
                'dateDebut': date_start.strftime('%Y-%m-%d') + 'T00:00:00.000Z',
                'dateFin': date_end.strftime('%Y-%m-%d') + 'T23:59:59.999Z',
            }
        )

        if r.status_code != 200:
            raise EcocitoException(f'Invalid HTTP response: {r.status_code}')

        try:
            return json.loads(r.content)
        except json.decoder.JSONDecodeError:
            html = bs(r.content, 'html.parser')
            error = html.find_all('div', { 'class': 'error' })

            if error:
                raise EcocitoException(error[0].text)
            else:
                raise


class State:
    KNOWN_HASHES = 'known_hashes'

    def __init__(self, state_file: Union[Path, str]):
        self._file = state_file
        if isinstance(self._file, str):
            self._file = Path(self._file)
        self._state = {}

    def load_state(self):
        if self._file.exists():
            with self._file.open('r') as f:
                self._state = json.load(f)

    def save_state(self):
        if not self._file.parent.exists():
            self._file.parent.mkdir(parents=True)

        with self._file.open('w') as f:
            json.dump(self._state, f)
        logging.debug('State saved in %s', self._file)

    def compute_hash(self, time: datetime, cuve: str, puce: str, weight: float) -> str:
        return '{}_{}_{}_{}'.format(
            time.isoformat(),
            cuve,
            puce,
            weight,
        )

    def is_new(self, time: datetime, cuve: str, puce: str, weight: float) -> bool:
        hash = self.compute_hash(time, cuve, puce, weight)
        logging.debug('Hash computed: %s', hash)
        if hash not in self._state.get(self.KNOWN_HASHES, []):
            if self.KNOWN_HASHES not in self._state:
                self._state[self.KNOWN_HASHES] = []
            self._state[self.KNOWN_HASHES].append(hash)
            self.save_state()
            return True

        return False


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(message)s')

    tz = timezone(os.getenv('TZ', 'UTC'))

    state = State(os.getenv('STATE_FILE', '/data/state.json'))
    state.load_state()

    mqtt_client = mqtt.Client()
    mqtt_client.connect(
        os.getenv('MQTT_BROKER')
    )

    while True:
        try:
            client = Ecocito(
                os.getenv('ECOCITO_SUBDOMAIN'),
                os.getenv('ECOCITO_USERNAME'),
                os.getenv('ECOCITO_PASSWORD')
            )
            client.login()

            data = client.get_levees(
                datetime.now() + relativedelta(months =- 2),
                datetime.now()
            )

            for row in data.get('data', []):
                cuve = row.get('NumeroCuve')
                puce = row.get('NumeroPuce')
                weight = row.get('QUANTITE_NETTE')
                time =  tz.localize(datetime.fromisoformat(row.get('DATE_DONNEE')))

                if state.is_new(time, cuve, puce, weight):
                    logging.info('New row detected: %s %s %s %f', time, cuve, puce, weight)

                    mqtt_client.publish(
                        os.getenv('MQTT_TOPIC', 'ecocito/levee'),
                        payload=json.dumps({
                            'time': time.isoformat(),
                            'cuve': cuve,
                            'puce': puce,
                            'weight': weight,
                        }),
                        qos=0,
                        retain=True,
                    )

            client.logout()
            
            logging.debug('Waiting 1h before fetching new data')
            t.sleep(3600)
        except Exception:
            logging.exception('Unable to fetch data')
