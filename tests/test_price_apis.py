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
                    {
                        'bitcoin': {'usd': 95000, 'usd_24h_change': -1.2},
                        'ethereum': {'usd': 3200, 'usd_24h_change': 3.4},
                    }
                ),
            ]
        )

        with patch('price_apis._build_session', return_value=session):
            api = CoinGecko(symbols='eth,btc')
            price_data = api.fetch_price_data()

        self.assertEqual([asset['symbol'] for asset in price_data], ['eth', 'btc'])

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
