"""Compatibility import/CLI entry; implementation: gateway/tool_filter.py."""
if __name__ == "__main__":
    import runpy
    runpy.run_module("gateway.tool_filter", run_name="__main__")
else:
    import importlib
    import sys
    sys.modules[__name__] = importlib.import_module("gateway.tool_filter")
