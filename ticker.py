#!/usr/bin/env python3

import gc
import os
import time
from pathlib import Path

from PIL import Image

from frame import Frame
from rgbmatrix import graphics
from price_apis import get_api_cls, logger

BASE_DIR = Path(__file__).resolve().parent
FONT_DIR = BASE_DIR / 'fonts'
ICON_DIR = BASE_DIR / 'icons'
ICON_SIZE = 12


class Ticker(Frame):
    def __init__(self, *args, **kwargs):
        """Initialize the Ticker class

        Gather the users settings from environment variables, then initialize the
        LED Panel Frame class.
        """
        self._cached_price_data = None
        self._last_success_time = 0.0
        self._next_retry_time = 0.0

        # Set up the API
        api_cls = get_api_cls(os.environ.get('API', 'coingecko'))
        self.api = api_cls(symbols=self.get_symbols(), currency=self.get_currency())

        # Get user settings
        self.refresh_rate = self.get_positive_int_setting('REFRESH_RATE', 300)
        self.sleep = self.get_positive_int_setting('SLEEP', 3)
        self.retry_delay = self.get_positive_int_setting(
            'RETRY_DELAY', min(30, self.refresh_rate)
        )

        # Pre-loaded at run() time after matrix is initialized
        self._fonts = {}
        self._icons = {}

        super().__init__(*args, **kwargs)

    def _load_fonts(self):
        """Load all fonts once and cache them."""
        font_paths = {
            'symbol': FONT_DIR / '7x13.bdf',
            'price': FONT_DIR / '6x12.bdf',
            'change': FONT_DIR / '6x10.bdf',
            'price_small': FONT_DIR / '5x8.bdf',
        }
        for name, path in font_paths.items():
            font = graphics.Font()
            font.LoadFont(str(path))
            self._fonts[name] = font

    def _load_icons(self):
        """Load all available crypto icons once and cache as PIL images."""
        if not ICON_DIR.is_dir():
            return
        for path in sorted(ICON_DIR.iterdir()):
            if path.suffix.lower() != '.png':
                continue
            symbol = path.stem.lower()
            try:
                with Image.open(path) as source_image:
                    icon = source_image.convert('RGB')

                if icon.width > ICON_SIZE or icon.height > ICON_SIZE:
                    icon = icon.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)

                self._icons[symbol] = icon
                logger.info(f'Loaded icon for {symbol}')
            except Exception as e:
                logger.warning(f'Failed to load icon {path.name}: {e}')

    def get_positive_int_setting(self, setting_name, default_value):
        raw_value = os.environ.get(setting_name)
        if raw_value in (None, ''):
            return default_value

        try:
            parsed_value = int(raw_value)
        except ValueError:
            logger.warning(
                'Invalid %s=%r. Falling back to %s.',
                setting_name,
                raw_value,
                default_value,
            )
            return default_value

        if parsed_value < 1:
            logger.warning(
                '%s must be greater than 0. Falling back to %s.',
                setting_name,
                default_value,
            )
            return default_value

        return parsed_value

    def get_symbols(self):
        """Get the symbols to include"""
        symbols = os.environ.get('SYMBOLS', 'btc,eth')
        if not symbols:
            return 'btc,eth'
        return symbols

    def get_currency(self):
        """Get the currency to use"""
        currency = os.environ.get('CURRENCY', 'usd')
        if not currency:
            return 'usd'
        return currency

    @property
    def price_data(self):
        """Price data for the requested assets, updated automatically.

        Returns cached data unless the cache is stale per REFRESH_RATE.
        On fetch failure, returns stale cache rather than None.
        """
        now = time.monotonic()
        cache_is_stale = (
            not self._cached_price_data
            or (now - self._last_success_time) > self.refresh_rate
        )

        if self._cached_price_data and not cache_is_stale:
            return self._cached_price_data

        if now < self._next_retry_time:
            return self._cached_price_data

        price_data = self.api.fetch_price_data()
        fetch_completed_at = time.monotonic()

        if price_data:
            self._cached_price_data = price_data
            self._last_success_time = fetch_completed_at
            self._next_retry_time = 0.0
        elif self._cached_price_data:
            logger.warning('Fetch failed, using stale cached data.')
            self._next_retry_time = fetch_completed_at + self.retry_delay
        else:
            logger.error('No price data available.')
            self._next_retry_time = fetch_completed_at + self.retry_delay

        return self._cached_price_data

    def get_ticker_canvas(self, asset):
        """Build the ticker canvas given an asset.

        Layout on 64x32 display:
          Row 0-12:  [icon 12x12] [SYMBOL]        [change%]
          Row 13-31:              [price]

        If an icon exists for the symbol, it's drawn at top-left and the
        symbol text shifts right. Otherwise text-only layout is used.
        """
        canvas = self.matrix.CreateFrameCanvas()
        canvas.Clear()

        font_symbol = self._fonts['symbol']
        font_change = self._fonts['change']
        font_price = (
            self._fonts['price_small']
            if len(asset['price']) > 10
            else self._fonts['price']
        )

        # Check for icon
        symbol_lower = asset['symbol'].lower()
        icon = self._icons.get(symbol_lower)

        # X offset for symbol text depends on whether we have an icon
        symbol_x = (ICON_SIZE + 2) if icon else 3

        # Right-align the change percentage
        change_width = sum(
            [font_change.CharacterWidth(ord(c)) for c in asset['change_24h']]
        )
        change_x = 62 - change_width

        # Colors
        main_color = graphics.Color(255, 255, 0)
        change_color = (
            graphics.Color(194, 24, 7)
            if asset['change_24h'].startswith('-')
            else graphics.Color(46, 139, 87)
        )

        # Draw icon if available
        if icon:
            canvas.SetImage(icon, 0, 0)

        # Draw text elements
        graphics.DrawText(canvas, font_symbol, symbol_x, 12, main_color, asset['symbol'])
        graphics.DrawText(canvas, font_price, 3, 28, main_color, asset['price'])
        graphics.DrawText(
            canvas, font_change, change_x, 10, change_color, asset['change_24h']
        )

        return canvas

    def get_error_canvas(self):
        """Build an error canvas to show on errors"""
        canvas = self.matrix.CreateFrameCanvas()
        canvas.Clear()
        font = self._fonts['symbol']
        color = graphics.Color(194, 24, 7)
        graphics.DrawText(canvas, font, 15, 20, color, 'ERROR')
        return canvas

    def get_assets(self):
        """Generator method that yields assets infinitely.

        Handles empty/None price data gracefully by yielding None and
        waiting before retrying, rather than spinning or crashing.
        """
        while True:
            data = self.price_data
            if not data:
                yield None
                continue

            for asset in data:
                yield asset

    def run(self):
        """Run the loop and display ticker prices.

        Catches all exceptions to prevent the display from going blank.
        Runs periodic garbage collection to keep memory stable on Pi W.
        """
        self._load_fonts()
        self._load_icons()

        frame_count = 0
        for asset in self.get_assets():
            try:
                if asset:
                    canvas = self.get_ticker_canvas(asset)
                else:
                    canvas = self.get_error_canvas()
                self.matrix.SwapOnVSync(canvas)
            except Exception as e:
                logger.error(f'Error rendering frame: {e}')
                try:
                    canvas = self.get_error_canvas()
                    self.matrix.SwapOnVSync(canvas)
                except Exception:
                    pass

            time.sleep(self.sleep)

            # Periodic GC to prevent memory creep on constrained Pi W
            frame_count += 1
            if frame_count % 100 == 0:
                gc.collect()
                frame_count = 0


if __name__ == '__main__':
    Ticker().process()
