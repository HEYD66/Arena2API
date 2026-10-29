"""Compatibility import/CLI entry; implementation: gateway/openai_compat.py."""
if __name__ == "__main__":
    import runpy
    runpy.run_module("gateway.openai_compat", run_name="__main__")
else:
    import importlib
    import sys
    sys.modules[__name__] = importlib.import_module("gateway.openai_compat")
