"""Run the local gateway without changing the project configuration directory."""
import runpy
runpy.run_module('gateway.server', run_name='__main__')
