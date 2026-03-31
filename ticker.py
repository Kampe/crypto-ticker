#!/usr/bin/env python3

import gc
import os
import time

from PIL import Image

from frame import Frame
from rgbmatrix import graphics
from price_apis import get_api_cls, logger

ICON_DIR = os.path.join(os.path.dirname(__file__), 'icons')
ICON_SIZE = 12


class Ticker(Frame):
    def __init__(self, *args, **kwargs):
        """Initialize the Ticker class

        Gather the users settings from environment variables, then initialize the
        LED Panel Frame class.
        """
        self._cached_price_data = None
        self._last_fetch_time = 0

        # Set up the API
        api_cls = get_api_cls(os.environ.get('API', 'coingecko'))
        self.api = api_cls(symbols=self.get_symbols(), currency=self.get_currency())

        # Get user settings
        self.refresh_rate = int(os.environ.get('REFRESH_RATE', 300))  # 300s or 5m
        self.sleep = int(os.environ.get('SLEEP', 3))  # 3s

        # Pre-loaded at run() time after matrix is initialized
        self._fonts = {}
        self._icons = {}

        super().__init__(*args, **kwargs)

    def _load_fonts(self):
        """Load all fonts once and cache them."""
        font_paths = {
            'symbol': 'fonts/7x13.bdf',
            'price': 'fonts/6x12.bdf',
            'change': 'fonts/6x10.bdf',
            'price_small': 'fonts/5x8.bdf',
        }
        for name, path in font_paths.items():
            font = graphics.Font()
            font.LoadFont(path)
            self._fonts[name] = font

    def _load_icons(self):
        """Load all available crypto icons once and cache as PIL images."""
        if not os.path.isdir(ICON_DIR):
            return
        for filename in os.listdir(ICON_DIR):
            if not filename.endswith('.png'):
                continue
            symbol = filename[:-4].lower()
            path = os.path.join(ICON_DIR, filename)
            try:
                img = Image.open(path).convert('RGB')
                # Ensure icon fits the display constraints
                if img.width > ICON_SIZE or img.height > ICON_SIZE:
                    img = img.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
                self._icons[symbol] = img
                logger.info(f'Loaded icon for {symbol}')
            except Exception as e:
                logger.warning(f'Failed to load icon {filename}: {e}')

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
        cache_is_stale = (time.time() - self._last_fetch_time) > self.refresh_rate

        if self._cached_price_data and not cache_is_stale:
            return self._cached_price_data

        price_data = self.api.fetch_price_data()
        self._last_fetch_time = time.time()

        if price_data:
            self._cached_price_data = price_data
        elif self._cached_price_data:
            logger.warning('Fetch failed, using stale cached data.')
        else:
            logger.error('No price data available.')

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
                time.sleep(self.refresh_rate)
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
