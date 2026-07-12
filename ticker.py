#!/usr/bin/env python3

import gc
import math
import os
import re
import time
from collections import defaultdict, deque
from pathlib import Path

from PIL import Image, ImageDraw

from frame import Frame
from rgbmatrix import graphics
from price_apis import get_api_cls, logger

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
        self.animation_fps = self.get_positive_int_setting('ANIMATION_FPS', 8)
        self.overview_every = self.get_positive_int_setting('OVERVIEW_EVERY', 1)

        self._fonts = {}
        self._icons = {}
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
        """Load all available crypto icons once and cache as RGB PIL images."""
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

    def _background(self, asset, frame_index):
        """Draw a low-cost animated background as a PIL image."""
        symbol = asset['symbol'].lower() if asset else 'market'
        primary, secondary = self._asset_colors(symbol)
        image = Image.new('RGB', (self.width, self.height), (1, 3, 8))
        draw = ImageDraw.Draw(image)

        for y in range(self.height):
            t = y / float(max(1, self.height - 1))
            wave = (math.sin((frame_index * 0.31) + (y * 0.42)) + 1.0) / 2.0
            color = mix(dim(primary, 0.18), dim(secondary, 0.26), (t + wave) / 2.0)
            draw.line((0, y, self.width, y), fill=color)

        sweep_x = (frame_index * 5) % (self.width + 16) - 8
        draw.rectangle((sweep_x, 0, sweep_x + 2, self.height), fill=dim(secondary, 0.55))
        draw.line((0, self.height - 1, self.width, self.height - 1), fill=dim(secondary, 0.35))
        for x in range((frame_index % 8) - 8, self.width, 8):
            draw.point((x, 0), fill=dim(primary, 0.75))
            draw.point((x + 3, self.height - 2), fill=dim(secondary, 0.65))
        return image

    def _draw_icon_badge(self, image, asset, frame_index):
        draw = ImageDraw.Draw(image)
        symbol = asset['symbol'].lower()
        primary, secondary = self._asset_colors(symbol)
        pulse = 0.55 + 0.25 * math.sin(frame_index * 0.45)
        draw.rectangle((0, 0, 14, 14), outline=dim(secondary, 0.85), fill=dim(primary, pulse))
        draw.rectangle((1, 1, 13, 13), outline=dim(primary, 1.2))

        icon = self._icons.get(symbol)
        if icon:
            image.paste(icon, (1, 1))
        else:
            # Small monogram fallback; real symbol text is drawn with rgbmatrix later.
            draw.rectangle((4, 4, 10, 10), outline=dim(secondary, 0.9), fill=dim(primary, 0.45))

    def _draw_sparkline(self, image, asset, frame_index):
        symbol = asset['symbol'].lower()
        values = list(self._price_history[symbol])
        if len(values) < 2:
            value = asset.get('price_value') or self._parse_price(asset.get('price', '')) or 1.0
            values = [value * 0.995, value, value * 1.003]

        primary, secondary = self._asset_colors(symbol)
        draw = ImageDraw.Draw(image)
        left = 35
        top = 15
        right = self.width - 2
        bottom = self.height - 3
        low = min(values)
        high = max(values)
        span = high - low or max(high, 1.0) * 0.001

        points = []
        for idx, value in enumerate(values[-HISTORY_POINTS:]):
            t = idx / float(max(1, min(len(values), HISTORY_POINTS) - 1))
            x = int(left + t * (right - left))
            y = int(bottom - ((value - low) / span) * (bottom - top))
            points.append((x, y))

        for offset, color in ((1, dim(primary, 0.28)), (0, secondary)):
            shifted = [(x, y + offset) for x, y in points]
            if len(shifted) > 1:
                draw.line(shifted, fill=color)

        last_x, last_y = points[-1]
        blink = frame_index % 8 < 4
        if blink:
            draw.rectangle((last_x - 1, last_y - 1, last_x + 1, last_y + 1), fill=(255, 255, 255))

    def _draw_market_meter(self, image, asset):
        draw = ImageDraw.Draw(image)
        change = self._change_value(asset)
        positive = change >= 0
        color = (34, 255, 136) if positive else (255, 55, 95)
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
        self._draw_icon_badge(image, asset, frame_index)
        self._draw_sparkline(image, asset, frame_index)
        self._draw_market_meter(image, asset)
        canvas = self._base_canvas_from_image(image)

        change = asset.get('change_24h', '0.0%')
        change_value = self._change_value(asset)
        change_color = (40, 255, 150) if change_value >= 0 else (255, 62, 105)
        symbol_color = (255, 244, 170)
        price_color = (235, 255, 225)

        symbol = asset['symbol'].lower()
        if len(symbol) <= 4:
            self._text(canvas, 'symbol', 16, 13, symbol_color, symbol)
        else:
            self._text(canvas, 'price', 16, 11, symbol_color, symbol[:6])

        change_width = self._font_width(self._fonts['micro'], change)
        change_x = max(30, self.width - change_width - 2)
        self._text(canvas, 'micro', change_x, 7, change_color, change)

        price = asset['price']
        font_name = 'price' if len(price) <= 10 else 'micro'
        y = 29 if font_name == 'price' else 28
        self._text(canvas, font_name, 2, y, price_color, price)
        return canvas

    def get_overview_canvas(self, price_data, frame_index=0):
        image = self._background(price_data[0] if price_data else None, frame_index)
        draw = ImageDraw.Draw(image)

        cols = 3
        tile_w = self.width // cols
        tile_h = self.height // 2
        for index, asset in enumerate(price_data[:6]):
            col = index % cols
            row = index // cols
            x = col * tile_w
            y = row * tile_h
            symbol = asset['symbol'].lower()[:4]
            change = self._change_value(asset)
            primary, secondary = self._asset_colors(symbol)
            color = (34, 255, 136) if change >= 0 else (255, 62, 105)
            draw.rectangle((x, y, x + tile_w - 1, y + tile_h - 1), outline=dim(secondary, 0.45))

        canvas = self._base_canvas_from_image(image)
        for index, asset in enumerate(price_data[:6]):
            col = index % cols
            row = index // cols
            x = col * tile_w
            y = row * tile_h
            symbol = asset['symbol'].lower()[:4]
            change = self._change_value(asset)
            primary, _secondary = self._asset_colors(symbol)
            color = (34, 255, 136) if change >= 0 else (255, 62, 105)
            self._text(canvas, 'micro', x + 1, y + 7, mix(primary, (255, 255, 255), 0.35), symbol)
            self._text(canvas, 'micro', x + 1, y + 15, color, f'{change:+.1f}')

        self._text(canvas, 'micro', self.width - 20, self.height - 1, (120, 220, 255), 'LIVE')
        return canvas

    def get_error_canvas(self, frame_index=0):
        image = self._background(None, frame_index)
        draw = ImageDraw.Draw(image)
        draw.rectangle((3, 6, self.width - 4, self.height - 7), outline=(255, 62, 105), fill=(24, 4, 12))
        canvas = self._base_canvas_from_image(image)
        self._text(canvas, 'symbol', 10, 20, (255, 62, 105), 'ERROR')
        return canvas

    def _show_canvas(self, canvas):
        swapped_canvas = self.matrix.SwapOnVSync(canvas)
        if swapped_canvas is not None:
            self._canvas = swapped_canvas

    def _show_asset(self, asset):
        frames = max(1, int(self.sleep * self.animation_fps))
        delay = 1.0 / float(self.animation_fps)
        for frame_index in range(frames):
            self._show_canvas(self.get_ticker_canvas(asset, frame_index))
            time.sleep(delay)

    def _show_overview(self, price_data):
        frames = max(1, int(min(2, self.sleep) * self.animation_fps))
        delay = 1.0 / float(self.animation_fps)
        for frame_index in range(frames):
            self._show_canvas(self.get_overview_canvas(price_data, frame_index))
            time.sleep(delay)

    def _show_error(self):
        frames = max(1, int(self.sleep * self.animation_fps))
        delay = 1.0 / float(self.animation_fps)
        for frame_index in range(frames):
            self._show_canvas(self.get_error_canvas(frame_index))
            time.sleep(delay)

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
        """Run the animated ticker loop with stale-data resilience."""
        self._load_fonts()
        self._load_icons()

        cycle_count = 0
        while True:
            try:
                data = self.price_data
                if not data:
                    self._show_error()
                else:
                    for asset in data:
                        self._show_asset(asset)
                    cycle_count += 1
                    if self.overview_every and cycle_count % self.overview_every == 0:
                        self._show_overview(data)
            except Exception as e:
                logger.error(f'Error rendering frame: {e}')
                try:
                    self._show_error()
                except Exception:
                    pass

            gc.collect()


if __name__ == '__main__':
    Ticker().process()
