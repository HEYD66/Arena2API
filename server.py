"""Compatibility import/CLI entry; implementation: gateway/server.py."""
if __name__ == "__main__":
    import runpy
    runpy.run_module("gateway.server", run_name="__main__")
else:
    import importlib
    import sys
    sys.modules[__name__] = importlib.import_module("gateway.server")
