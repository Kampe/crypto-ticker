import sys
import types


def install_fake_requests_modules():
    if 'requests' in sys.modules:
        return

    requests_module = types.ModuleType('requests')
    requests_module.RequestException = Exception
    requests_module.Session = type('Session', (), {})

    adapters_module = types.ModuleType('requests.adapters')
    adapters_module.HTTPAdapter = type('HTTPAdapter', (), {})

    urllib3_module = types.ModuleType('urllib3')
    urllib3_util_module = types.ModuleType('urllib3.util')
    urllib3_retry_module = types.ModuleType('urllib3.util.retry')
    urllib3_retry_module.Retry = type('Retry', (), {})

    sys.modules['requests'] = requests_module
    sys.modules['requests.adapters'] = adapters_module
    sys.modules['urllib3'] = urllib3_module
    sys.modules['urllib3.util'] = urllib3_util_module
    sys.modules['urllib3.util.retry'] = urllib3_retry_module


def install_fake_rgbmatrix_module():
    if 'rgbmatrix' in sys.modules:
        return

    class FakeFont:
        def LoadFont(self, _path):
            return None

        def CharacterWidth(self, _char_code):
            return 6

    rgbmatrix_module = types.ModuleType('rgbmatrix')
    rgbmatrix_module.graphics = types.SimpleNamespace(
        Font=FakeFont,
        Color=lambda *args: args,
        DrawText=lambda *args, **kwargs: None,
    )
    rgbmatrix_module.RGBMatrix = type('RGBMatrix', (), {})
    rgbmatrix_module.RGBMatrixOptions = type('RGBMatrixOptions', (), {})
    sys.modules['rgbmatrix'] = rgbmatrix_module
