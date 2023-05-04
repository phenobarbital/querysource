import asyncio
import os
from pathlib import Path
from typing import Any
from navconfig import BASE_DIR
from navconfig.logging import logging
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    MetricAggregation,
    OrderBy,
    Pivot,
    RunPivotReportRequest,
    RunReportRequest,
)
# google analytics ga4
from google.api_core.exceptions import PermissionDenied, ServiceUnavailable
from querysource.conf import GA_SERVICE_ACCOUNT_NAME, GA_SERVICE_PATH
from querysource.exceptions import QueryError, ConfigError
from querysource.libs.json import json_encoder
from .rest import restSource


class ga(restSource):
    """
      Google Analytics GA4 API
        Get all information from Google Analytics
    """
    method: str = 'get'

    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:

        # first: get type of Call
        try:
            self.type = definition.params['type']
        except (ValueError, AttributeError, KeyError):
            self.type = None
        try:
            self.type = conditions['type']
            del conditions['type']
        except (ValueError, AttributeError, KeyError):
            pass

        # property ID:
        if 'property_id' in self._conditions:
            self.property_id = self._conditions['property_id']
            del self._conditions['property_id']
        else:
            ## Default Propertiy ID:
            self.property_id = self._env.get('GA_PROPERTY_ID')
            if not self.property_id:
                try:
                    self.property_id = definition.params['GA_PROPERTY_ID']
                except (ValueError, AttributeError) as err:
                    raise ValueError(
                        "Google Analytics: Missing Property ID"
                    ) from err

        # service account name (json)
        ### Passing Credentials File directly from conditions
        self._credentials = {}
        if 'account_name' in self._conditions:
            self._credentials = self._conditions['account_name']
            del self._conditions['account_name']
        else:
            self._credentials = self._env.get('GA_CREDENTIALS')

        # getting credentials:
        if not self._credentials:
            if 'project_id' in self._conditions:
                ### try to build file by ourself
                project_id = self._conditions["project_id"]
                del self._conditions["project_id"]
                try:
                    account_prefix = self._conditions["account_prefix"]
                    del self._conditions["account_prefix"]
                    private_key_id = self._env.get(f'{account_prefix}_PRIVATE_KEY_ID')
                    private_key = os.environ[f'{account_prefix}_PRIVATE_KEY'].replace('\\n', '\n')
                    client_id = self._env.get(f'{account_prefix}_CLIENT_ID')
                    account_email = self._env.get(f'{account_prefix}_CLIENT_EMAIL')
                except (KeyError, ValueError) as ex:
                    raise ConfigError(
                        "Missing *account_prefix* to extract credentials from ENV"
                    ) from ex
                credentials = {
                    "type": "service_account",
                    "project_id": project_id,
                    "private_key_id": private_key_id,
                    "private_key": f"-----BEGIN PRIVATE KEY-----\n{private_key}\n-----END PRIVATE KEY-----\n",
                    "client_email": account_email,
                    "client_id": client_id,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "client_x509_cert_url": f"https://www.googleapis.com/robot/v1/metadata/x509/{account_email}"
                }
                filename = BASE_DIR.joinpath('google', f'{project_id}.json')
                if not filename.exists():
                    self.logger.notice(
                        f"GA4 File Path: {filename!s}"
                    )
                    try:
                        with open(filename, mode='w', encoding='utf-8') as f:
                            f.write(json_encoder(credentials))
                    except Exception as ex:
                        raise RuntimeError(
                            f"Can't create Google GA4 credentials Filename {filename}, {ex}"
                        ) from ex
                self._credentials = str(filename)
            else:
                # read from file:
                filename = None
                try:
                    f = self._env.get('GA_SERVICE_ACCOUNT_NAME')
                    filename = Path(f).resolve()
                except Exception:
                    pass
                if not filename:
                    filename = BASE_DIR.joinpath(GA_SERVICE_PATH, GA_SERVICE_ACCOUNT_NAME)
                if not filename or not filename.exists():
                    raise ValueError(
                        f"Google Analytics: Missing Service Account Name or Google Credentials: {filename!s}"
                    )
                self._credentials = str(filename)
        print('CREDENTIALS: ', self._credentials)
        ### start configuring
        if self._credentials:
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(self._credentials)
        else:
            raise ValueError(
                "Google Analytics: Missing Google JSON Credentials"
            )

        # date range:
        if 'startdate' in self._conditions:
            self._conditions['start_date'] = self._conditions['startdate']
        if not self._conditions['start_date']:
            raise ValueError(
                "Google Analytics: Missing Start Date range for comparison"
            )
        if 'enddate' in self._conditions:
            self._conditions['end_date'] = self._conditions['enddate']
        if not 'end_date' in self._conditions:
            self._conditions['end_date'] = "today"

        # set parameters
        self._args = {}

    async def report(self):
        """
            report any dimensions you need.
            Return a collection of dimensions from GA4.
        """
        client = BetaAnalyticsDataClient()
        metrics = []
        order_by = []
        if 'metric' in self._conditions:
            if isinstance(self._conditions['metric'], list):
                for metric in self._conditions['metric']:
                    metrics.append(
                        Metric(name=metric)
                    )
            else:
                metrics.append(
                    Metric(name=self._conditions['metric'])
                )
        else:
            metrics.append(
                    Metric(name='activeUsers')
                )
        dimensions = []
        if 'dimensions' in self._conditions:
            for dimension in self._conditions['dimensions']:
                dimensions.append(
                    Dimension(name=dimension)
                )
        else:
            dimensions.append(
                Dimension(name='userGender')
            )
        # ordering:
        if 'order_by' in self._conditions:
            order_by.append(
                OrderBy(
                    dimension=OrderBy.DimensionOrderBy(
                        dimension_name=self._conditions['order_by']
                    ),
                    desc=True
                )
            )
        try:
            request = RunReportRequest(
                property=f"properties/{self.property_id}",
                dimensions=dimensions,
                metrics=metrics,
                date_ranges=[
                    DateRange(
                        start_date=self._conditions['start_date'],
                        end_date=self._conditions['end_date'])
                ],
                keep_empty_rows=True,
                metric_aggregations=[
                    MetricAggregation.TOTAL,
                    MetricAggregation.MAXIMUM,
                    MetricAggregation.MINIMUM,
                ],
                order_bys=order_by
            )
            # print(request, dimensions, metrics)
            response = client.run_report(request)
            self._result = await self.run_report(response)
            return self._result
        except Exception as err:
            logging.exception(err)
            raise QueryError(
                str(err)
            ) from err

    async def pivot_report(self):
        """
            report any dimensions you need.
            Return a collection of dimensions from GA4.
        """
        client = BetaAnalyticsDataClient()
        metrics = []
        if 'metric' in self._conditions:
            if isinstance(self._conditions['metric'], list):
                for metric in self._conditions['metric']:
                    metrics.append(
                        Metric(name=metric)
                    )
            else:
                metrics.append(
                    Metric(name=self._conditions['metric'])
                )
        else:
            metrics.append(
                    Metric(name='activeUsers')
                )
        dimensions = []
        pivots = []
        if 'dimensions' in self._conditions:
            for dimension in self._conditions['dimensions']:
                dimensions.append(
                    Dimension(name=dimension)
                )
                pivots.append(
                    Pivot(
                        field_names=[dimension],
                        limit=250,
                    )
                )
        else:
            dimensions.append(
                Dimension(name='userGender')
            )
        try:
            request = RunPivotReportRequest(
                property=f"properties/{self.property_id}",
                dimensions=dimensions,
                metrics=metrics,
                date_ranges=[
                    DateRange(
                        start_date=self._conditions['start_date'],
                        end_date=self._conditions['end_date'])
                ],
                keep_empty_rows=True,
                pivots=pivots
            )
            response = client.run_pivot_report(request)
            print(request, dimensions, metrics)
            self._result = await self.run_report(response)
            return self._result
        except Exception as err:
            logging.exception(err)
            raise Exception from err

    async def run_report(self, response):
        try:
            await asyncio.sleep(1)
            # print(response)
            print(f"{response.row_count} rows received")
            dimensions = [d.name for d in response.dimension_headers]
            metrics = [m.name for m in response.metric_headers]
            # print('DIMENSIONS > ', dimensions, metrics)
            result = []
            for row in response.rows:
                # print('ROW ', row)
                el = {}
                i = 0
                for dimension in dimensions:
                    el[dimension] = row.dimension_values[i].value
                    i+=1
                # for dimension_value in row.dimension_values:
                #     print('DIMENSIONS> ', dimension_value.value)
                i = 0
                for metric in metrics:
                    el[metric] = row.metric_values[i].value
                    i+=1
                result.append(el)
            return result
        except PermissionDenied as err:
            raise Exception(
                f"GA: Permission Denied: {err}"
            ) from err
        except ServiceUnavailable as err:
            raise Exception(
                f"GA: Service Unavailable: {err}"
            ) from err
        except Exception as err:
            raise Exception from err
