# Crypto Ticker

A library for displaying crypto asset prices on an LED matrix panel using a Raspberry Pi.

Requires:

  * Adafruit 64x32 LED Matrix Panel
  * Raspberry Pi Zero WH
  * CoinGecko or CoinMarketCap API account

See the Howchoo guide for installation and configuration instructions:

https://howchoo.com/pi/raspberry-pi-cryptocurrency-ticker

## Settings

You can customize the application by adding any of the following settings to your settings.env file in the root directory of this repo:


| Name | Default | Description |
|--|--|--|
| SYMBOLS | btc,eth | The asset symbols you want to track. |
| CURRENCY | usd | The currency used to show asset prices. CoinGecko currently supports "usd" and "eur", while CoinMarketCap supports only "usd". |
| API | coingecko | The API you want to use to fetch price data. Currently supported APIs are "coingecko" and "coinmarketcap". |
| REFRESH_RATE | 300 | How often to refresh price data, in seconds. |
| SLEEP | 3 | How long each asset price displays before rotating, in seconds. |
| RETRY_DELAY | 30 or REFRESH_RATE if lower | How long to wait before retrying a failed refresh while stale data remains on screen. |
| COINGECKO\_API\_KEY | | Optional CoinGecko API key. Recommended for the 24h market chart requests. |
| COINGECKO\_API\_TIER | demo | CoinGecko key type. Use "demo" for `x-cg-demo-api-key` or "pro" for `x-cg-pro-api-key`. |
| LED\_BRIGHTNESS | 60 | Matrix brightness. Lower values reduce visible scan flicker and glare. |
| LED\_PWM\_BITS | 7 | Matrix PWM bit depth. Lower values improve refresh rate on Pi Zero hardware. |
| LED\_PWM\_LSB\_NANOSECONDS | 100 | Matrix PWM timing. Lower values can reduce flicker if the panel remains stable. |
| LED\_SLOWDOWN\_GPIO | 1 | RGB matrix GPIO slowdown setting. Increase only if the panel shows corrupted pixels. |
| CMC\_API\_KEY | | The CoinMarketCap API key, required if you specified API=coinmarketcap. |
| SANDBOX | | Used for CoinMarketCap only. Set SANDBOX=false if you're developing and want to use the sandbox API. |

Example:

```
SYMBOLS=btc,eth,ltc,xrp
API=coingecko
```

CoinGecko charts use the `coins/markets` sparkline data and render the most recent 24 samples across the LED panel background. The line is green when the chart ends above its first point and red when it ends below.

Note: Some symbols are ambiguous. For example, `uni` currently corresponds to three different currencies in the CoinGecko API. To specify the
currency or token you want (with CoinGecko only), you can use the following:

```
SYMBOLS=btc,eth,uni:uniswap
```

The second value (uniswap) corresponds to the ID of the currency in the API. This is currently only supported for the CoinGecko API. You can
find the CoinGecko ID for a token in the URL. E.g. https://www.coingecko.com/en/coins/uniswap.

## Native systemd service

For Raspberry Pi Zero W installs, running the ticker natively under systemd is more reliable than building Docker images on-device.

```bash
sudo cp systemd/crypto-ticker.service /etc/systemd/system/crypto-ticker.service
sudo systemctl daemon-reload
sudo systemctl enable --now crypto-ticker.service
```

The service starts on boot, restarts on failure, and recycles every 12 hours to avoid long-running memory creep on constrained Pi Zero hardware.
