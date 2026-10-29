"""Compatibility import/CLI entry; implementation: gateway/client_attachments.py."""
if __name__ == "__main__":
    import runpy
    runpy.run_module("gateway.client_attachments", run_name="__main__")
else:
    import importlib
    import sys
    sys.modules[__name__] = importlib.import_module("gateway.client_attachments")
