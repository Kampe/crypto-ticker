import json
import logging
import os
import sys

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# Set up the logger -- INFO level to avoid flooding stdout/docker logs
logger = logging.getLogger('crypto-ticker')
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.propagate = False

# Shared timeout for all HTTP requests (connect, read) in seconds
REQUEST_TIMEOUT = (5, 15)

API_CLASS_MAP = {'coinmarketcap': 'CoinMarketCap', 'coingecko': 'CoinGecko'}


def _build_session():
    """Build a requests Session with retry logic and connection pooling."""
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retries, pool_maxsize=2)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_api_cls(api_name):
    """
    Args:
        api_name (str): The name of the API to use.
    """
    if api_name not in API_CLASS_MAP:
        raise RuntimeError(f'"{api_name}" api is not implemented.')
    return getattr(sys.modules[__name__], API_CLASS_MAP[api_name])


class PriceAPI:
    """The base class for Price API"""

    def __init__(self, symbols, currency='usd'):
        self._symbols = symbols
        self._requested_assets = self._parse_symbols(symbols)
        self.currency = currency
        self.validate_currency(currency)
        self.session = _build_session()

    def _parse_symbols(self, symbols):
        requested_assets = []
        for raw_symbol in symbols.split(','):
            symbol_value = raw_symbol.strip().lower()
            if not symbol_value:
                continue

            symbol, _, coin_id = symbol_value.partition(':')
            requested_assets.append((symbol, coin_id or None))

        return requested_assets

    def get_symbols(self):
        """Get a list of symbols needed"""
        return [symbol for symbol, _coin_id in self._requested_assets]

    def get_name_for_symbol(self, symbol):
        """Return the name for the symbol, if specified"""
        for requested_symbol, coin_id in self._requested_assets:
            if symbol == requested_symbol:
                return coin_id
        return None

    def order_price_data(self, price_data):
        ordered_assets = []
        assets_by_symbol = {
            asset['symbol'].lower(): asset for asset in price_data if 'symbol' in asset
        }

        for symbol in self.get_symbols():
            asset = assets_by_symbol.get(symbol)
            if asset is not None:
                ordered_assets.append(asset)

        return ordered_assets

    def fetch_price_data(self):
        """Fetch new price data from the API.

        Returns:
            A list of dicts that represent price data for a single asset. For example:

            [{'symbol': .., 'price': .., 'change_24h': ..}]
        """
        raise NotImplementedError

    @property
    def supported_currencies(self):
        raise NotImplementedError

    def validate_currency(self, currency):
        if currency not in self.supported_currencies:
            raise ValueError(
                f"CURRENCY={currency} is not supported. Options are: {self.supported_currencies}."
            )


class CoinMarketCap(PriceAPI):
    SANDBOX_API = 'https://sandbox-api.coinmarketcap.com'
    PRODUCTION_API = 'https://pro-api.coinmarketcap.com'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        try:
            self.api_key = os.environ['CMC_API_KEY']
        except KeyError:
            raise RuntimeError('CMC_API_KEY environment variable must be set.')

        self.api_url = (
            self.SANDBOX_API
            if os.environ.get('SANDBOX', '') == 'true'
            else self.PRODUCTION_API
        )

    @property
    def supported_currencies(self):
        return ["usd"]

    def fetch_price_data(self):
        """Fetch new price data from the CoinMarketCap API"""
        logger.info('Fetching price data from CoinMarketCap.')

        try:
            response = self.session.get(
                f'{self.api_url}/v1/cryptocurrency/quotes/latest',
                params={'symbol': ','.join(self.get_symbols())},
                headers={'X-CMC_PRO_API_KEY': self.api_key},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f'CoinMarketCap API request failed: {e}')
            return None

        try:
            items = response.json().get('data', {}).items()
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f'JSON decode error: {e}')
            return None

        price_data = []
        for symbol, data in items:
            try:
                price = f"${data['quote']['USD']['price']:,.2f}"
                change_24h = f"{data['quote']['USD']['percent_change_24h']:.1f}%"
            except (KeyError, TypeError) as e:
                logger.warning(f'Incomplete data for {symbol}: {e}')
                continue
            price_data.append(dict(symbol=symbol, price=price, change_24h=change_24h))

        return self.order_price_data(price_data)


class CoinGecko(PriceAPI):
    API = 'https://api.coingecko.com/api/v3'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.symbol_map = {}
        self._fetch_coin_list()

    def _fetch_coin_list(self):
        """Fetch the CoinGecko coin list and build a symbol -> id mapping."""
        try:
            response = self.session.get(
                f'{self.API}/coins/list', timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            coins = response.json()
        except (requests.RequestException, ValueError) as e:
            logger.error(f'Failed to fetch CoinGecko coin list: {e}')
            return

        symbols = self.get_symbols()
        symbol_map = {}

        for coin in coins:
            symbol = coin['symbol']
            name = self.get_name_for_symbol(symbol)
            if name is not None and name != coin['id']:
                continue
            if symbol in symbols:
                symbol_map[coin['id']] = symbol

        self.symbol_map = symbol_map
        resolved_symbols = set(symbol_map.values())
        missing_symbols = [
            symbol for symbol in symbols if symbol not in resolved_symbols
        ]
        if missing_symbols:
            logger.warning(
                'Could not resolve CoinGecko symbols: %s',
                ', '.join(missing_symbols),
            )

    @property
    def supported_currencies(self):
        return ["usd", "eur"]

    def fetch_price_data(self):
        """Fetch new price data from the CoinGecko API"""
        if not self.symbol_map:
            logger.warning('No symbol map available, retrying coin list fetch.')
            self._fetch_coin_list()
            if not self.symbol_map:
                return None

        logger.info(f'Fetching prices for: {list(self.symbol_map.values())}')

        try:
            response = self.session.get(
                f'{self.API}/simple/price',
                params={
                    'ids': ','.join(self.symbol_map.keys()),
                    'vs_currencies': self.currency,
                    'include_24hr_change': 'true',
                },
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as e:
            logger.error(f'CoinGecko API request failed: {e}')
            return None

        cur = self.currency
        cur_change = f"{cur}_24h_change"
        cur_symbol = "\u20ac" if cur == "eur" else "$"

        price_data = []
        for coin_id, coin_data in data.items():
            try:
                price = f"{cur_symbol}{coin_data[cur]:,.2f}"
                change_24h = f"{coin_data[cur_change]:.1f}%"
            except (KeyError, TypeError):
                logger.warning(f'Incomplete data for {coin_id}: {coin_data}')
                continue

            price_data.append(
                dict(
                    symbol=self.symbol_map.get(coin_id, coin_id),
                    price=price,
                    change_24h=change_24h,
                )
            )

        return self.order_price_data(price_data)
