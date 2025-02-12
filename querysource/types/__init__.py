from datamodel.typedefs import SafeDict, Singleton
from .validators import is_boolean, is_empty, strtobool

# MIME Types: based on file extension.
mime_types = {
    'application/json': 'json',
    'text/plain': 'txt',
    'application/octet-stream': 'object',
    'text/csv': 'csv',
    'text/tsv': 'tsv',
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'application/vnd.oasis.opendocument.text': 'odt',
    'application/vnd.oasis.opendocument.spreadsheet': 'ods',
    'application/pdf': 'pdf',
    'image/svg+xml': 'svg',
    'application/vnd.ms-excel': 'xls',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
    'application/vnd.ms-excel.sheet.macroEnabled.12': 'xlsm',
    'application/xml': 'xml',
    'text/html': 'html',
    'application/xhtml+xml': 'html',
    'application/xhtml': 'html',
    'video/x-msvideo': 'avi'
}

# MIME Formats supported by the API.
mime_formats = {
    'plain': 'text/plain',
    'raw': 'text/plain',
    'json': 'application/json',
    'table': 'application/json',
    'dt': 'application/json',
    'txt': 'text/plain',
    'object': 'application/octet-stream',
    'csv': 'text/csv',
    'tsv': 'text/tsv',
    'jpg': 'image/jpeg',
    'png': 'image/png',
    'odt': 'application/vnd.oasis.opendocument.text',
    'ods': 'application/vnd.oasis.opendocument.spreadsheet',
    'pdf': 'application/pdf',
    'svg': 'image/svg+xml',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'excel': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'xlsm': 'application/vnd.ms-excel.sheet.macroEnabled.12',
    'xml': 'application/xml',
    'html': 'text/html',
    'avi': 'video/x-msvideo',
    'bokeh': 'text/html',
    'plotly': 'text/html',
    'pickle': 'application/octet-stream',
    'profiling': 'text/html',
    'report': 'text/html',
    'describe': 'application/json',
    'eda': 'text/html',
    'clustering': 'text/csv'
}

# MIME Types supported by the API.
mime_supported = {
    'text/plain': 'txt',
    'application/json': 'json',
    'application/octet-stream': 'pickle',
    'text/csv': 'csv',
    'text/tsv': 'tsv',
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'application/vnd.oasis.opendocument.text': 'odt',
    'application/vnd.oasis.opendocument.spreadsheet': 'ods',
    'application/pdf': 'pdf',
    'image/svg+xml': 'svg',
    'application/vnd.ms-excel': 'excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'excel',
    'application/vnd.ms-excel.sheet.macroEnabled.12': 'excel',
    'application/xml': 'xml',
    'application/atom+xml': 'xml',
    'application/rss+xml': 'xml',
    'application/rdf+xml': 'xml',
    'text/html': 'html',
    'video/x-msvideo': 'avi'
}


# Graph outputs supported by the API.
graph_ouputs = (
    'plotly',
    'bokeh',
    'matplotlib',
    'seaborn',
)


__all__ = (
    'SafeDict',
    'Singleton',
    'strtobool',
    'mime_formats',
    'graph_ouputs',
    'mime_supported',
)
