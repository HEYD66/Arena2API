"""Compatibility import/CLI entry; implementation: gateway/gateway_log.py."""
if __name__ == "__main__":
    import runpy
    runpy.run_module("gateway.gateway_log", run_name="__main__")
else:
    import importlib
    import sys
    sys.modules[__name__] = importlib.import_module("gateway.gateway_log")
