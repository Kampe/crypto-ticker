import os
import unittest
from unittest.mock import patch

from tests.support import install_fake_requests_modules

install_fake_requests_modules()

from price_apis import CoinGecko


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class CoinGeckoTests(unittest.TestCase):
    def test_fetch_price_data_returns_assets_in_requested_order(self):
        session = FakeSession(
            [
                FakeResponse(
                    [
                        {'id': 'bitcoin', 'symbol': 'btc'},
                        {'id': 'ethereum', 'symbol': 'eth'},
                    ]
                ),
                FakeResponse(
                    [
                        {
                            'id': 'bitcoin',
                            'current_price': 95000,
                            'price_change_percentage_24h': -1.2,
                            'image': 'https://example.test/btc.png',
                        },
                        {
                            'id': 'ethereum',
                            'current_price': 3200,
                            'price_change_percentage_24h': 3.4,
                            'image': 'https://example.test/eth.png',
                        },
                    ]
                ),
                FakeResponse({'prices': [[1, 94000], [2, 95000]]}),
                FakeResponse({'prices': [[1, 3100], [2, 3200]]}),
            ]
        )

        with patch('price_apis._build_session', return_value=session):
            api = CoinGecko(symbols='eth,btc')
            price_data = api.fetch_price_data()

        self.assertEqual([asset['symbol'] for asset in price_data], ['eth', 'btc'])
        self.assertEqual(price_data[0]['image_url'], 'https://example.test/eth.png')
        self.assertEqual(price_data[0]['history_24h'], [3100.0, 3200.0])

    def test_uses_demo_api_key_header_when_configured(self):
        session = FakeSession([FakeResponse([])])

        with patch.dict(os.environ, {'COINGECKO_API_KEY': 'secret'}, clear=False):
            with patch('price_apis._build_session', return_value=session):
                CoinGecko(symbols='btc')

        _url, kwargs = session.calls[0]
        self.assertEqual(kwargs['headers'], {'x-cg-demo-api-key': 'secret'})

    def test_uses_pro_api_key_header_when_configured(self):
        session = FakeSession([FakeResponse([])])

        with patch.dict(
            os.environ,
            {'COINGECKO_API_KEY': 'secret', 'COINGECKO_API_TIER': 'pro'},
            clear=False,
        ):
            with patch('price_apis._build_session', return_value=session):
                CoinGecko(symbols='btc')

        url, kwargs = session.calls[0]
        self.assertTrue(url.startswith(CoinGecko.PRO_API))
        self.assertEqual(kwargs['headers'], {'x-cg-pro-api-key': 'secret'})

    def test_fetch_coin_list_warns_when_requested_symbol_is_missing(self):
        session = FakeSession(
            [
                FakeResponse(
                    [
                        {'id': 'bitcoin', 'symbol': 'btc'},
                    ]
                )
            ]
        )

        with patch('price_apis._build_session', return_value=session):
            with self.assertLogs('crypto-ticker', level='WARNING') as captured_logs:
                api = CoinGecko(symbols='btc,doge')

        self.assertEqual(api.symbol_map, {'bitcoin': 'btc'})
        self.assertTrue(
            any('doge' in message for message in captured_logs.output),
            msg=f'Expected missing symbol warning in logs: {captured_logs.output}',
        )


if __name__ == '__main__':
    unittest.main()
