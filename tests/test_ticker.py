import importlib
import os
import tempfile
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch

from PIL import Image

from tests.support import install_fake_requests_modules, install_fake_rgbmatrix_module

install_fake_requests_modules()
install_fake_rgbmatrix_module()

ticker = importlib.import_module('ticker')


class SequencedAPI:
    next_responses = []
    instances = []

    def __init__(self, symbols, currency):
        self.symbols = symbols
        self.currency = currency
        self.responses = list(self.__class__.next_responses)
        self.fetch_calls = 0
        self.__class__.instances.append(self)

    def fetch_price_data(self):
        self.fetch_calls += 1
        return self.responses.pop(0)


class TickerTests(unittest.TestCase):
    def setUp(self):
        SequencedAPI.instances.clear()
        SequencedAPI.next_responses = []

    def test_price_data_retries_after_short_backoff_when_refresh_fails(self):
        initial_data = [{'symbol': 'btc', 'price': '$95,000.00', 'change_24h': '1.0%'}]
        updated_data = [{'symbol': 'btc', 'price': '$96,000.00', 'change_24h': '2.0%'}]
        SequencedAPI.next_responses = [initial_data, None, updated_data]
        monotonic_values = iter([0, 1, 350, 351, 362, 363])

        with patch.object(ticker, 'get_api_cls', return_value=SequencedAPI):
            with patch.dict(
                os.environ,
                {'REFRESH_RATE': '300', 'RETRY_DELAY': '10', 'SYMBOLS': 'btc'},
                clear=False,
            ):
                with patch.object(
                    ticker.time,
                    'time',
                    side_effect=lambda: next(monotonic_values),
                ):
                    with patch.object(
                        ticker.time,
                        'monotonic',
                        side_effect=lambda: next(monotonic_values),
                    ):
                        app = ticker.Ticker()

                        self.assertEqual(app.price_data, initial_data)
                        self.assertEqual(app.price_data, initial_data)
                        self.assertEqual(app.price_data, updated_data)

        self.assertEqual(SequencedAPI.instances[0].fetch_calls, 3)

    def test_remote_icon_replaces_builtin_once(self):
        app = ticker.Ticker.__new__(ticker.Ticker)
        app._icons = {'btc': Image.new('RGBA', (12, 12), (1, 2, 3, 255))}
        app._remote_icon_attempted = set()

        remote_image = Image.new('RGBA', (64, 64), (255, 140, 0, 255))
        payload = BytesIO()
        remote_image.save(payload, format='PNG')

        response = MagicMock()
        response.content = payload.getvalue()
        response.raise_for_status.return_value = None

        with tempfile.TemporaryDirectory() as icon_dir:
            with patch.object(ticker, 'REMOTE_ICON_DIR', ticker.Path(icon_dir)):
                with patch.object(
                    ticker.requests, 'get', return_value=response, create=True
                ) as get:
                    app._cache_remote_icon(
                        {'symbol': 'btc', 'image_url': 'https://example.test/btc.png'}
                    )
                    app._cache_remote_icon(
                        {'symbol': 'btc', 'image_url': 'https://example.test/btc.png'}
                    )

        self.assertEqual(get.call_count, 1)
        self.assertEqual(app._icons['btc'].size, (12, 12))
        self.assertEqual(app._icons['btc'].getpixel((5, 5)), (255, 140, 0, 255))


if __name__ == '__main__':
    unittest.main()
