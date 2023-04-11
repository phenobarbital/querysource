import sys
from querysource.conf import PLUGINS_FOLDER
from .importer import PluginImporter


### add plugins directory to sys.path
sys.path.insert(0, str(PLUGINS_FOLDER))

### Sources Loader.
sources_dir = PLUGINS_FOLDER.joinpath('sources')
package_name = 'querysource.plugins.sources'
try:
    sys.meta_path.append(PluginImporter(package_name, str(sources_dir)))
except ImportError as exc:
    print(exc)
