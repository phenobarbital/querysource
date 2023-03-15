"""Supported Outputs for QuerySource.
"""
from .output import DataOutput

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

__all__ = ('DataOutput', )
