from typing import Optional
from datamodel import BaseModel, Field
from datamodel.types import JSON_TYPES
from ...conf import (
    # BigQuery Default Credentials
    BIGQUERY_CREDENTIALS,
    BIGQUERY_PROJECT_ID,
)

class bigqueryDriver(BaseModel):
    driver: str = Field(required=True, primary_key=True, default='bigquery')
    driver_type: str = Field(
        required=True,
        default='asyncdb',
        comment="type of driver, can be asyncdb, QS or REST"
    )
    name: str = Field(required=False, comment='Google BigQuery.')
    description: str = Field(comment='Google Big Query', repr=False)
    dsn: str = Field(required=False)
    dsn_format: str = Field(required=False, default=None, repr=False)
    icon: str = Field(required=False, comment='Icon Path for Datasource.', repr=False)
    credentials: str = Field(required=True, comment='env/bigquery.json')
    project_id: str = Field(required=True, comment='Google BigQuery.')
    dataset: str = Field(required=False)
    required_properties: Optional[tuple] = Field(repr=False, default=('credentials', 'project_id'))

    def params(self) -> dict:
        """params

        Returns:
            dict: params required for AsyncDB.
        """
        return {
            "credentials": self.credentials,
            "project_id": self.project_id,
        }

    def get_credentials(self) -> dict:
        return {
            "credentials": self.credentials,
            "project_id": self.project_id
        }

    def get_parameters(self) -> dict:
        return {
            "credentials": self.credentials,
            "project_id": self.project_id,
            "dataset": self.dataset,
        }

    @classmethod
    def properties(cls) -> dict:
        """properties.

        Description: Returns fields related to Drivers Supported.
        Returns:
            dict: all required fields for Supported Drivers.
        """
        fields = {}
        for field in cls.required_properties:
            # because tuple is ordered:
            try:
                f = cls.column(cls, field)
            except KeyError:
                continue  # Field Missing on Driver:
            secret = False
            if 'is_secret' in f.metadata:
                secret = f.metadata["is_secret"]
            title = field
            if 'title' in f.metadata:
                title = f.metadata['title']
            required = False
            if 'required' in f.metadata:
                required = f.metadata['required']
            try:
                _type = JSON_TYPES[f.type]
            except KeyError:
                _type = str(f.type)
            f = {
                "name": field,
                "title": title,
                "required": required,
                "type": _type,
                "is_secret": secret
            }
            value = getattr(cls, field)
            default = hasattr(f, 'default')
            if not value and default:
                value = f.default
            if value:
                f["value"] = value
            fields[field] = f
        return {
            "driver": cls.driver,
            "name": cls.name,
            "icon": cls.icon,
            "dsn_format": cls.dsn_format,
            "fields": fields
        }
try:
    bigquery_default = bigqueryDriver(
        credentials=BIGQUERY_CREDENTIALS,
        project_id=BIGQUERY_PROJECT_ID
    )
except Exception:
    bigquery_default = None
