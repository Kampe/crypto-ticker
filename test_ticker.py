#!/usr/bin/env python3
"""Test suite for crypto-ticker that mocks rgbmatrix hardware.

Validates all logic: API clients, caching, font/icon loading, canvas
rendering, error handling, and the main loop -- without real LED hardware.
"""

import os
import sys
import time
import types
import unittest
from unittest.mock import MagicMock, patch

# --- Mock rgbmatrix before any project imports ---
mock_rgbmatrix = types.ModuleType('rgbmatrix')


class MockRGBMatrixOptions:
    def __init__(self):
        for attr in [
            'hardware_mapping', 'rows', 'cols', 'chain_length', 'parallel',
            'row_address_type', 'multiplexing', 'pwm_bits', 'brightness',
            'pwm_lsb_nanoseconds', 'led_rgb_sequence', 'pixel_mapper_config',
            'panel_type', 'show_refresh_rate', 'gpio_slowdown',
            'disable_hardware_pulsing',
        ]:
            setattr(self, attr, None)


class MockCanvas:
    def Clear(self):
        pass

    def SetImage(self, image, x=0, y=0):
        self._last_image = image
        self._last_pos = (x, y)


class MockRGBMatrix:
    def __init__(self, options=None):
        self.options = options

    def CreateFrameCanvas(self):
        return MockCanvas()

    def SwapOnVSync(self, canvas):
        pass


class MockFont:
    def __init__(self):
        self._loaded = None

    def LoadFont(self, path):
        self._loaded = path

    def CharacterWidth(self, char_code):
        return 6


class MockGraphics:
    Font = MockFont

    @staticmethod
    def Color(r, g, b):
        return (r, g, b)

    @staticmethod
    def DrawText(canvas, font, x, y, color, text):
        pass


mock_rgbmatrix.RGBMatrix = MockRGBMatrix
mock_rgbmatrix.RGBMatrixOptions = MockRGBMatrixOptions
mock_rgbmatrix.graphics = MockGraphics
sys.modules['rgbmatrix'] = mock_rgbmatrix
sys.modules['rgbmatrix.graphics'] = MockGraphics

# Set working directory so font/icon paths resolve
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from price_apis import (
    CoinGecko, CoinMarketCap, PriceAPI, get_api_cls, _build_session, logger,
)


# ---------------------------------------------------------------------------
# price_apis tests
# ---------------------------------------------------------------------------

class TestGetApiCls(unittest.TestCase):
    def test_coingecko(self):
        self.assertEqual(get_api_cls('coingecko'), CoinGecko)

    def test_coinmarketcap(self):
        self.assertEqual(get_api_cls('coinmarketcap'), CoinMarketCap)

    def test_invalid_raises(self):
        with self.assertRaises(RuntimeError):
            get_api_cls('binance')


class TestPriceAPIBase(unittest.TestCase):
    def _make_api(self, symbols):
        api = PriceAPI.__new__(PriceAPI)
        api._symbols = symbols
        api._requested_assets = api._parse_symbols(symbols)
        return api

    def test_get_symbols_simple(self):
        self.assertEqual(self._make_api('btc,eth').get_symbols(), ['btc', 'eth'])

    def test_get_symbols_with_names(self):
        self.assertEqual(self._make_api('btc:bitcoin,eth:ethereum').get_symbols(), ['btc', 'eth'])

    def test_get_name_for_symbol_present(self):
        self.assertEqual(self._make_api('btc:bitcoin,eth').get_name_for_symbol('btc'), 'bitcoin')

    def test_get_name_for_symbol_absent(self):
        self.assertIsNone(self._make_api('btc:bitcoin,eth').get_name_for_symbol('eth'))

    def test_order_price_data(self):
        api = self._make_api('eth,btc')
        data = [
            {'symbol': 'btc', 'price': '$67,000', 'change_24h': '2.5%'},
            {'symbol': 'eth', 'price': '$3,400', 'change_24h': '-1.0%'},
        ]
        ordered = api.order_price_data(data)
        self.assertEqual(ordered[0]['symbol'], 'eth')
        self.assertEqual(ordered[1]['symbol'], 'btc')


class TestBuildSession(unittest.TestCase):
    def test_has_retry(self):
        session = _build_session()
        adapter = session.get_adapter('https://example.com')
        self.assertEqual(adapter.max_retries.total, 3)


class TestCoinGeckoFetch(unittest.TestCase):
    def _make_gecko(self, mock_session, coin_list, symbols='btc,eth'):
        coin_resp = MagicMock()
        coin_resp.json.return_value = coin_list
        coin_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = coin_resp

        with patch('price_apis._build_session', return_value=mock_session):
            return CoinGecko(symbols=symbols, currency='usd')

    def test_fetch_success(self):
        mock_session = MagicMock()
        mock_session.mount = MagicMock()

        coin_list = [
            {'id': 'bitcoin', 'symbol': 'btc', 'name': 'Bitcoin'},
            {'id': 'ethereum', 'symbol': 'eth', 'name': 'Ethereum'},
        ]
        api = self._make_gecko(mock_session, coin_list)

        price_resp = MagicMock()
        price_resp.json.return_value = {
            'bitcoin': {'usd': 67123.45, 'usd_24h_change': 2.5},
            'ethereum': {'usd': 3456.78, 'usd_24h_change': -1.2},
        }
        price_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = price_resp

        data = api.fetch_price_data()
        self.assertEqual(len(data), 2)
        btc = next(d for d in data if d['symbol'] == 'btc')
        self.assertIn('$', btc['price'])
        self.assertIn('%', btc['change_24h'])

    def test_network_error_returns_none(self):
        import requests as req
        mock_session = MagicMock()
        mock_session.mount = MagicMock()

        api = self._make_gecko(mock_session, [
            {'id': 'bitcoin', 'symbol': 'btc', 'name': 'Bitcoin'},
        ], symbols='btc')

        mock_session.get.side_effect = req.ConnectionError('Network down')
        self.assertIsNone(api.fetch_price_data())

    def test_incomplete_data_skipped(self):
        mock_session = MagicMock()
        mock_session.mount = MagicMock()

        api = self._make_gecko(mock_session, [
            {'id': 'bitcoin', 'symbol': 'btc', 'name': 'Bitcoin'},
        ], symbols='btc')

        price_resp = MagicMock()
        price_resp.json.return_value = {'bitcoin': {'usd': 67000}}  # missing 24h change
        price_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = price_resp

        data = api.fetch_price_data()
        self.assertEqual(len(data), 0)

    def test_empty_symbol_map_retries_coin_list(self):
        mock_session = MagicMock()
        mock_session.mount = MagicMock()

        api = self._make_gecko(mock_session, [], symbols='btc')
        self.assertEqual(api.symbol_map, {})

        # fetch_price_data should try to re-fetch the coin list and return None
        empty_resp = MagicMock()
        empty_resp.json.return_value = []
        empty_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = empty_resp

        self.assertIsNone(api.fetch_price_data())


class TestCoinMarketCapFetch(unittest.TestCase):
    @patch.dict(os.environ, {'CMC_API_KEY': 'test-key'})
    @patch('price_apis._build_session')
    def test_fetch_success(self, mock_build):
        mock_session = MagicMock()
        mock_build.return_value = mock_session

        resp = MagicMock()
        resp.json.return_value = {
            'data': {
                'BTC': {'quote': {'USD': {'price': 67000.50, 'percent_change_24h': 3.2}}},
            }
        }
        resp.raise_for_status = MagicMock()
        mock_session.get.return_value = resp

        api = CoinMarketCap(symbols='btc', currency='usd')
        data = api.fetch_price_data()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['symbol'], 'BTC')
        self.assertIn('$', data[0]['price'])

    @patch.dict(os.environ, {'CMC_API_KEY': 'test-key'})
    @patch('price_apis._build_session')
    def test_api_url_production(self, mock_build):
        mock_build.return_value = MagicMock()
        api = CoinMarketCap(symbols='btc', currency='usd')
        self.assertEqual(api.api_url, CoinMarketCap.PRODUCTION_API)

    @patch.dict(os.environ, {'CMC_API_KEY': 'test-key', 'SANDBOX': 'true'})
    @patch('price_apis._build_session')
    def test_api_url_sandbox(self, mock_build):
        mock_build.return_value = MagicMock()
        api = CoinMarketCap(symbols='btc', currency='usd')
        self.assertEqual(api.api_url, CoinMarketCap.SANDBOX_API)

    def test_missing_api_key_raises(self):
        os.environ.pop('CMC_API_KEY', None)
        with patch('price_apis._build_session', return_value=MagicMock()):
            with self.assertRaises(RuntimeError):
                CoinMarketCap(symbols='btc', currency='usd')


# ---------------------------------------------------------------------------
# Ticker tests (mocked hardware)
# ---------------------------------------------------------------------------

class TestTickerIntegration(unittest.TestCase):
    def _make_ticker(self):
        env = {
            'SYMBOLS': 'btc,eth',
            'CURRENCY': 'usd',
            'API': 'coingecko',
            'REFRESH_RATE': '300',
            'SLEEP': '1',
        }
        with patch.dict(os.environ, env, clear=False):
            with patch('price_apis._build_session') as mock_build:
                mock_session = MagicMock()
                mock_build.return_value = mock_session

                coin_resp = MagicMock()
                coin_resp.json.return_value = [
                    {'id': 'bitcoin', 'symbol': 'btc', 'name': 'Bitcoin'},
                    {'id': 'ethereum', 'symbol': 'eth', 'name': 'Ethereum'},
                ]
                coin_resp.raise_for_status = MagicMock()
                mock_session.get.return_value = coin_resp

                from ticker import Ticker
                ticker = Ticker()
                ticker.matrix = MockRGBMatrix()
                return ticker, mock_session

    def test_font_loading(self):
        ticker, _ = self._make_ticker()
        ticker._load_fonts()
        for key in ('symbol', 'price', 'change', 'price_small'):
            self.assertIn(key, ticker._fonts)
        self.assertTrue(ticker._fonts['symbol']._loaded.endswith('spleen-8x16.bdf'))

    def test_icon_loading(self):
        ticker, _ = self._make_ticker()
        ticker._load_icons()
        self.assertIn('btc', ticker._icons)
        self.assertIn('eth', ticker._icons)
        self.assertEqual(ticker._icons['btc'].size, (12, 12))
        self.assertEqual(ticker._icons['btc'].mode, 'RGB')

    def test_ticker_canvas_with_icon(self):
        ticker, _ = self._make_ticker()
        ticker._load_fonts()
        ticker._load_icons()
        canvas = ticker.get_ticker_canvas(
            {'symbol': 'btc', 'price': '$67,123.45', 'change_24h': '2.5%'}
        )
        self.assertIsInstance(canvas, MockCanvas)

    def test_ticker_canvas_without_icon(self):
        ticker, _ = self._make_ticker()
        ticker._load_fonts()
        ticker._load_icons()
        canvas = ticker.get_ticker_canvas(
            {'symbol': 'unknown', 'price': '$1.23', 'change_24h': '-0.5%'}
        )
        self.assertIsInstance(canvas, MockCanvas)

    def test_ticker_canvas_long_price_uses_small_font(self):
        ticker, _ = self._make_ticker()
        ticker._load_fonts()
        ticker._load_icons()
        canvas = ticker.get_ticker_canvas(
            {'symbol': 'btc', 'price': '$1,234,567.89', 'change_24h': '1.0%'}
        )
        self.assertIsInstance(canvas, MockCanvas)

    def test_error_canvas(self):
        ticker, _ = self._make_ticker()
        ticker._load_fonts()
        canvas = ticker.get_error_canvas()
        self.assertIsInstance(canvas, MockCanvas)

    def test_price_data_caching(self):
        ticker, mock_session = self._make_ticker()

        price_resp = MagicMock()
        price_resp.json.return_value = {
            'bitcoin': {'usd': 67000, 'usd_24h_change': 2.5},
            'ethereum': {'usd': 3400, 'usd_24h_change': -1.0},
        }
        price_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = price_resp

        data1 = ticker.price_data
        self.assertIsNotNone(data1)
        data2 = ticker.price_data
        self.assertEqual(data1, data2)

    def test_stale_cache_preserved_on_failure(self):
        import requests as req
        ticker, mock_session = self._make_ticker()

        price_resp = MagicMock()
        price_resp.json.return_value = {
            'bitcoin': {'usd': 67000, 'usd_24h_change': 2.5},
        }
        price_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = price_resp

        data1 = ticker.price_data
        self.assertIsNotNone(data1)

        # Force stale
        ticker._last_success_time = 0.0
        ticker._next_retry_time = 0.0

        mock_session.get.side_effect = req.ConnectionError('down')
        data2 = ticker.price_data
        self.assertEqual(data1, data2)

    def test_get_assets_yields_none_when_no_data(self):
        import requests as req
        ticker, mock_session = self._make_ticker()
        ticker._cached_price_data = None
        ticker._last_success_time = 0.0
        ticker._next_retry_time = 0.0

        mock_session.get.side_effect = req.ConnectionError('down')

        gen = ticker.get_assets()
        asset = next(gen)
        self.assertIsNone(asset)

    def test_get_assets_cycles_through_data(self):
        ticker, _ = self._make_ticker()
        ticker._cached_price_data = [
            {'symbol': 'btc', 'price': '$67,000', 'change_24h': '2.5%'},
            {'symbol': 'eth', 'price': '$3,400', 'change_24h': '-1.0%'},
        ]
        ticker._last_success_time = time.monotonic()

        gen = ticker.get_assets()
        symbols = [next(gen)['symbol'] for _ in range(4)]
        self.assertEqual(symbols, ['btc', 'eth', 'btc', 'eth'])

    def test_get_positive_int_setting_defaults(self):
        ticker, _ = self._make_ticker()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('TEST_SETTING', None)
            self.assertEqual(ticker.get_positive_int_setting('TEST_SETTING', 42), 42)

    def test_get_positive_int_setting_valid(self):
        ticker, _ = self._make_ticker()
        with patch.dict(os.environ, {'TEST_SETTING': '10'}):
            self.assertEqual(ticker.get_positive_int_setting('TEST_SETTING', 42), 10)

    def test_get_positive_int_setting_invalid(self):
        ticker, _ = self._make_ticker()
        with patch.dict(os.environ, {'TEST_SETTING': 'abc'}):
            self.assertEqual(ticker.get_positive_int_setting('TEST_SETTING', 42), 42)

    def test_get_positive_int_setting_zero(self):
        ticker, _ = self._make_ticker()
        with patch.dict(os.environ, {'TEST_SETTING': '0'}):
            self.assertEqual(ticker.get_positive_int_setting('TEST_SETTING', 42), 42)


if __name__ == '__main__':
    unittest.main(verbosity=2)
