import types
import importlib
from importlib.machinery import SourceFileLoader
import os

class PluginImporter(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, package_name, plugins_path):
        self.package_name = package_name
        self.plugins_path = plugins_path

    def find_spec(self, fullname, path, target=None):
        if fullname.startswith(self.package_name + '.'):
            component_name = fullname.split('.')[-1]
            component_path = os.path.join(self.plugins_path, f"{component_name}.py")

            if os.path.exists(component_path):
                return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        component_name = module.__name__.split('.')[-1]
        component_path = os.path.join(self.plugins_path, f"{component_name}.py")
        loader = SourceFileLoader(component_name, component_path)
        loaded = types.ModuleType(loader.name)
        loader.exec_module(loaded)
        # Update the module's namespace with the loaded module's namespace
        module.__dict__.update(loaded.__dict__)
