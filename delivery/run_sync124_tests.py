"""Run the regression suite against the deployed single file, without network access."""
import importlib.util
from pathlib import Path
import sys
import unittest

root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('tested_sync_service',root/'product_sync_service.py')
service=importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)
package=sys.modules[service._PACKAGE]
sys.modules['product_sync']=package
for name in service._SOURCES:
    module=sys.modules[service._PACKAGE+'.'+name]
    sys.modules['product_sync.'+name]=module
    setattr(package,name,module)
suite=unittest.defaultTestLoader.discover(str(root/'delivery/product_sync_1_2_4_tests'))
result=unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
