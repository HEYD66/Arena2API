"""Compatibility import/CLI entry; implementation: gateway/send_content.py."""
if __name__ == "__main__":
    import runpy
    runpy.run_module("gateway.send_content", run_name="__main__")
else:
    import importlib
    import sys
    sys.modules[__name__] = importlib.import_module("gateway.send_content")
