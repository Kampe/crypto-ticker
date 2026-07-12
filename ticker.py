#!/usr/bin/env python3

import gc
import os
import re
import time
from collections import defaultdict, deque
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw

from frame import Frame
from rgbmatrix import graphics
from price_apis import REQUEST_TIMEOUT, get_api_cls, logger

BASE_DIR = Path(__file__).resolve().parent
FONT_DIR = BASE_DIR / 'fonts'
ICON_DIR = BASE_DIR / 'icons'
ICON_SIZE = 12
HISTORY_POINTS = 18

ASSET_COLORS = {
    'btc': ((247, 147, 26), (255, 211, 94)),
    'eth': ((68, 121, 255), (129, 236, 255)),
    'gohm': ((138, 92, 246), (250, 204, 21)),
    'cvx': ((34, 197, 94), (163, 230, 53)),
    'crv': ((236, 72, 153), (96, 165, 250)),
    'blur': ((59, 130, 246), (244, 114, 182)),
    'ape': ((0, 190, 255), (20, 70, 180)),
    'sol': ((20, 241, 149), (153, 69, 255)),
    'doge': ((196, 154, 54), (255, 236, 179)),
    'ada': ((0, 122, 255), (125, 211, 252)),
    'link': ((42, 91, 220), (147, 197, 253)),
    'ltc': ((180, 180, 180), (99, 102, 241)),
    'xrp': ((220, 220, 220), (56, 189, 248)),
}


def clamp(value, lower=0, upper=255):
    return max(lower, min(upper, int(value)))


def mix(a, b, t):
    return tuple(clamp(a[i] + (b[i] - a[i]) * t) for i in range(3))


def dim(color, factor):
    return tuple(clamp(component * factor) for component in color)


class Ticker(Frame):
    def __init__(self, *args, **kwargs):
        """Initialize API/cache settings, then initialize the LED matrix frame."""
        self._cached_price_data = None
        self._last_success_time = 0.0
        self._next_retry_time = 0.0
        self._price_history = defaultdict(lambda: deque(maxlen=HISTORY_POINTS))

        api_cls = get_api_cls(os.environ.get('API', 'coingecko'))
        self.api = api_cls(symbols=self.get_symbols(), currency=self.get_currency())

        self.refresh_rate = self.get_positive_int_setting('REFRESH_RATE', 300)
        self.sleep = self.get_positive_int_setting('SLEEP', 3)
        self.retry_delay = self.get_positive_int_setting(
            'RETRY_DELAY', min(30, self.refresh_rate)
        )
        self._fonts = {}
        self._icons = {}
        self._remote_icon_attempted = set()
        self._canvas = None

        super().__init__(*args, **kwargs)
        self.width = self.args['led_cols'] * self.args['led_chain']
        self.height = self.args['led_rows'] * self.args['led_parallel']

    def _load_fonts(self):
        """Load all rgbmatrix BDF fonts once and cache them."""
        font_paths = {
            'symbol': FONT_DIR / 'spleen-8x16.bdf',
            'price': FONT_DIR / 'spleen-6x12.bdf',
            'micro': FONT_DIR / 'spleen-5x8.bdf',
        }
        for name, path in font_paths.items():
            font = graphics.Font()
            font.LoadFont(str(path))
            self._fonts[name] = font
        self._fonts['change'] = self._fonts['micro']
        self._fonts['price_small'] = self._fonts['micro']

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
                    icon = source_image.convert('RGBA')

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

    def _numeric_series(self, values):
        series = []
        for value in values or []:
            try:
                series.append(float(value))
            except (TypeError, ValueError):
                continue
        return series

    def get_symbols(self):
        symbols = os.environ.get('SYMBOLS', 'btc,eth')
        return symbols or 'btc,eth'

    def get_currency(self):
        currency = os.environ.get('CURRENCY', 'usd')
        return currency or 'usd'

    @property
    def price_data(self):
        """Price data for requested assets, with stale-cache fallback."""
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
            self._record_history(price_data)
        elif self._cached_price_data:
            logger.warning('Fetch failed, using stale cached data.')
            self._next_retry_time = fetch_completed_at + self.retry_delay
        else:
            logger.error('No price data available.')
            self._next_retry_time = fetch_completed_at + self.retry_delay

        return self._cached_price_data

    def _record_history(self, price_data):
        for asset in price_data:
            value = asset.get('price_value')
            if value is None:
                value = self._parse_price(asset.get('price', ''))
            if value is not None:
                self._price_history[asset['symbol'].lower()].append(float(value))
            self._cache_remote_icon(asset)

    def _cache_remote_icon(self, asset):
        symbol = asset.get('symbol', '').lower()
        image_url = asset.get('image_url')
        if not symbol or not image_url or symbol in self._remote_icon_attempted:
            return
        self._remote_icon_attempted.add(symbol)

        icon_path = ICON_DIR / f'{symbol}.png'

        try:
            response = requests.get(image_url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            with Image.open(BytesIO(response.content)) as source_image:
                icon = source_image.convert('RGBA')
                icon.thumbnail((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
                self._icons[symbol] = icon.copy()

                if icon_path.exists():
                    logger.info(f'Loaded remote icon for {symbol} without disk cache')
                    return

                ICON_DIR.mkdir(parents=True, exist_ok=True)
                icon.save(icon_path)
                logger.info(f'Cached icon for {symbol}')
        except Exception as e:
            logger.warning(f'Failed to cache icon for {symbol}: {e}')

    def _parse_price(self, formatted_price):
        match = re.search(r'[-+]?[0-9][0-9,]*(?:\.[0-9]+)?', formatted_price)
        if not match:
            return None
        try:
            return float(match.group(0).replace(',', ''))
        except ValueError:
            return None

    def _asset_colors(self, symbol):
        return ASSET_COLORS.get(symbol.lower(), ((56, 189, 248), (250, 204, 21)))

    def _change_value(self, asset):
        if 'change_value' in asset:
            return asset['change_value']
        try:
            return float(asset.get('change_24h', '0').replace('%', ''))
        except ValueError:
            return 0.0

    def _compact_change(self, change_value):
        if abs(change_value) < 1:
            text = f'{change_value:+.1f}'
        else:
            text = f'{change_value:+.0f}'
        return text.replace('+0.', '+.').replace('-0.', '-.')

    def _font_width(self, font, text):
        return sum(font.CharacterWidth(ord(char)) for char in text)

    def _text(self, canvas, font_name, x, y, color, text):
        graphics.DrawText(
            canvas,
            self._fonts[font_name],
            int(x),
            int(y),
            graphics.Color(*color),
            str(text),
        )

    def _background(self, asset, frame_index=0):
        """Draw an unlit background to minimize LED scan artifacts."""
        return Image.new('RGB', (self.width, self.height), (0, 0, 0))

    def _draw_icon(self, image, asset, frame_index=0):
        draw = ImageDraw.Draw(image)
        symbol = asset['symbol'].lower()
        primary, _secondary = self._asset_colors(symbol)

        icon = self._icons.get(symbol)
        if icon:
            image.paste(icon, (1, 1), icon if icon.mode == 'RGBA' else None)
        else:
            # Small monogram fallback; real symbol text is drawn with rgbmatrix later.
            draw.rectangle((4, 4, 10, 10), outline=dim(primary, 0.65))

    def _draw_sparkline(self, image, asset, frame_index=0):
        symbol = asset['symbol'].lower()
        values = self._numeric_series(asset.get('history_24h'))
        if len(values) < 2:
            values = list(self._price_history[symbol])

        _primary, secondary = self._asset_colors(symbol)
        draw = ImageDraw.Draw(image)
        left = 0
        top = 4
        right = self.width - 1
        bottom = self.height - 1
        if len(values) < 2:
            y = (top + bottom) // 2
            draw.line((left, y, right, y), fill=dim(secondary, 0.38))
            draw.point((right, y), fill=dim(secondary, 0.85))
            return

        low = min(values)
        high = max(values)
        span = high - low or max(high, 1.0) * 0.001
        line_color = (36, 210, 105) if values[-1] >= values[0] else (225, 48, 82)

        chart_values = self._bucket_series(values, max(2, right - left + 1))
        points = []
        for idx, value in enumerate(chart_values):
            t = idx / float(max(1, len(chart_values) - 1))
            x = int(round(left + t * (right - left)))
            y = int(bottom - ((value - low) / span) * (bottom - top))
            points.append((x, y))

        if len(points) > 1:
            draw.line(points, fill=line_color)

        last_x, last_y = points[-1]
        draw.point((last_x, last_y), fill=mix(line_color, (255, 255, 255), 0.25))

    def _bucket_series(self, values, bucket_count):
        if len(values) <= bucket_count:
            return values

        buckets = []
        value_count = len(values)
        for bucket in range(bucket_count):
            start = int(round(bucket * value_count / float(bucket_count)))
            end = int(round((bucket + 1) * value_count / float(bucket_count)))
            if end <= start:
                end = start + 1
            sample = values[start:end]
            buckets.append(sum(sample) / float(len(sample)))
        return buckets

    def _draw_market_meter(self, image, asset):
        draw = ImageDraw.Draw(image)
        change = self._change_value(asset)
        positive = change >= 0
        color = (20, 180, 90) if positive else (190, 35, 70)
        center = self.width - 5
        mid = 10
        height = int(min(8, max(1, abs(change) * 2)))
        if positive:
            draw.line((center, mid + 4, center, mid + 4 - height), fill=color)
            draw.point((center - 1, mid + 4 - height + 1), fill=color)
            draw.point((center + 1, mid + 4 - height + 1), fill=color)
        else:
            draw.line((center, mid - 4, center, mid - 4 + height), fill=color)
            draw.point((center - 1, mid - 4 + height - 1), fill=color)
            draw.point((center + 1, mid - 4 + height - 1), fill=color)

    def _base_canvas_from_image(self, image):
        canvas = self._canvas
        if canvas is None:
            canvas = self.matrix.CreateFrameCanvas()
        canvas.Clear()
        canvas.SetImage(image, 0, 0)
        return canvas

    def get_ticker_canvas(self, asset, frame_index=0):
        image = self._background(asset, frame_index)
        self._draw_sparkline(image, asset, frame_index)
        self._draw_icon(image, asset, frame_index)
        self._draw_market_meter(image, asset)
        canvas = self._base_canvas_from_image(image)

        change_value = self._change_value(asset)
        change = self._compact_change(change_value)
        change_color = (40, 190, 120) if change_value >= 0 else (210, 55, 90)
        symbol_color = (205, 196, 120)
        price_color = (185, 210, 180)

        symbol = asset['symbol'].lower()
        if len(symbol) <= 4:
            self._text(canvas, 'symbol', 16, 13, symbol_color, symbol)
        else:
            self._text(canvas, 'price', 16, 11, symbol_color, symbol[:6])

        change_width = self._font_width(self._fonts['micro'], change)
        change_x = max(45, self.width - change_width - 1)
        self._text(canvas, 'micro', change_x, 7, change_color, change)

        price = asset['price']
        font_name = 'price' if len(price) <= 10 else 'micro'
        y = 29 if font_name == 'price' else 28
        self._text(canvas, font_name, 2, y, price_color, price)
        return canvas

    def get_error_canvas(self, frame_index=0):
        image = self._background(None, frame_index)
        draw = ImageDraw.Draw(image)
        draw.rectangle((3, 6, self.width - 4, self.height - 7), outline=(190, 150, 40), fill=(18, 12, 4))
        canvas = self._base_canvas_from_image(image)
        self._text(canvas, 'symbol', 12, 19, (210, 170, 55), 'WAIT')
        self._text(canvas, 'micro', 17, 27, (120, 100, 50), 'API')
        return canvas

    def _show_canvas(self, canvas):
        swapped_canvas = self.matrix.SwapOnVSync(canvas)
        if swapped_canvas is not None:
            self._canvas = swapped_canvas

    def _show_asset(self, asset):
        self._show_canvas(self.get_ticker_canvas(asset, 0))
        time.sleep(self.sleep)

    def _show_error(self):
        self._show_canvas(self.get_error_canvas(0))
        time.sleep(self.sleep)

    def get_assets(self):
        """Yield assets indefinitely for compatibility with older tests/tools."""
        while True:
            data = self.price_data
            if not data:
                yield None
                continue

            for asset in data:
                yield asset

    def run(self):
        """Run the ticker loop with stale-data resilience."""
        self._load_fonts()
        self._load_icons()

        while True:
            try:
                data = self.price_data
                if not data:
                    self._show_error()
                else:
                    for asset in data:
                        self._show_asset(asset)
            except Exception as e:
                logger.error(f'Error rendering frame: {e}')
                try:
                    self._show_error()
                except Exception:
                    pass

            gc.collect()


if __name__ == '__main__':
    Ticker().process()
