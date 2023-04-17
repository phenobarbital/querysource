from typing import Any
from datetime import datetime, timedelta
from urllib.parse import urlencode
import pytz
from navconfig.logging import logging
from asyncdb.exceptions import NoDataFound
from querysource.exceptions import DataNotFound
from querysource.providers.sources import restSource


class venu(restSource):
    """VenU.

        Get all information from VenU API
    """
    base_url: str = 'https://{main_url}/lms/datacore/api/GET/{endpoint}/'

    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:
        self._headers['Content-Type'] = 'application/x-www-form-urlencoded'
        self.method = 'get'
        if 'main_url' in self._conditions:
            conditions['main_url'] = self._conditions['main_url']
            del self._conditions['main_url']
        else:
            conditions['main_url'] = self._env.get('VENU_BASE_URL')
            if not conditions['main_url']:
                try:
                    conditions['main_url'] = definition.params['main_url']
                except (ValueError, AttributeError) as ex:
                    raise ValueError("VenU: Missing Base URL") from ex
        self.url = 'https://{main_url}/lms/datacore/api/GET/{endpoint}/'
        # Credentials
        if 'username' in self._conditions:
            self._user = self._conditions['username']
            del self._conditions['username']
        else:
            self._user = self._env.get('VENU_USERNAME')
            if not self._user:
                try:
                    self._user = definition.params['username']
                except (ValueError, AttributeError) as ex:
                    raise ValueError("VenU: Missing UserName") from ex

        if 'password' in self._conditions:
            self._pwd = self._conditions['password']
            del self._conditions['password']
        else:
            self._pwd = self._env.get('VENU_PASSWORD')
            if not self._pwd:
                try:
                    self._pwd = definition.params['password']
                except (ValueError, AttributeError):
                    raise ValueError("VenU: Missing Password")

        #Delta Timestamp
        try:
            print('>>>', self._conditions)
            if 'delta_timestamp' in self._conditions:
                tz = pytz.timezone('America/New_York')
                dt = datetime.strptime(self._conditions['delta_timestamp'], "%Y-%m-%dT%H:%M:%S")
                dt = tz.localize(dt, is_dst=None)
                self._conditions['delta_timestamp'] = int((dt - datetime(1970, 1, 1, tzinfo=pytz.utc)).total_seconds())
                self._conditions['delta_timestamp']
            #else:
            #    self._conditions['delta_timestamp'] = (datetime.now() - timedelta(days = 14)).strftime('%s')
        except Exception as err:
            logging.error(
                f"VenU API: wrong date format: {err}"
            )
            raise ValueError(
                f"VenU: Wrong Date Format: {err}"
            ) from err

        try:
            self.type = definition.params['type']
        except (ValueError, AttributeError):
            self.type = None
        if 'type' in self._conditions:
            self.type = self._conditions['type']
            del self._conditions['type']

        if self.type == 'users':
            if 'delta_timestamp' in self._conditions:
                del self._conditions['delta_timestamp']
            if 'report_type' in self._conditions:
                self._conditions['reportType'] = self._conditions['report_type']
            else:
                self._conditions['reportType'] = 'UserAPI'
            if 'is_employee' in self._conditions:
                self._conditions['u_isemployee'] = self._conditions['is_employee']
            if 'user_status' in self._conditions:
                self._conditions['userstatus'] = self._conditions['user_status']
            conditions['endpoint'] = 'User'
        if self.type == 'curriculum_list':
            conditions['endpoint'] = 'CurriculumList'
        if self.type == 'curriculum_assignments':
            if not 'delta_timestamp' in self._conditions:
                self._conditions['delta_timestamp'] = (datetime.now() - timedelta(days = 14)).strftime('%s')
            conditions['endpoint'] = 'CurriculumAssignments'
        if self.type == 'curriculum_assignments_removed':
            if not 'delta_timestamp' in self._conditions:
                self._conditions['delta_timestamp'] = (datetime.now() - timedelta(days = 14)).strftime('%s')
            conditions['endpoint'] = 'CurriculumAssignmentsRemoved'
        if self.type == 'course_list':
            conditions['endpoint'] = 'CourseList'
        if self.type == 'course_enrollments':
            if not 'delta_timestamp' in self._conditions:
                self._conditions['delta_timestamp'] = (datetime.now() - timedelta(days = 14)).strftime('%s')
            conditions['endpoint'] = 'CourseEnrollments'
        if self.type == 'course_enrollments_removed':
            if not 'delta_timestamp' in self._conditions:
                self._conditions['delta_timestamp'] = (datetime.now() - timedelta(days = 14)).strftime('%s')
            conditions['endpoint'] = 'CourseEnrollmentsRemoved'
        if self.type == 'course_template_list':
            conditions['endpoint'] = 'CourseTemplateList'

        # set parameters
        self._args = conditions

    async def users(self):
        """users.

        Get All Users.
        """
        if 'delta_timestamp' in self._conditions:
            del self._conditions['delta_timestamp']
        if 'report_type' in self._conditions:
            self._conditions['reportType'] = self._conditions['report_type']
        else:
            self._conditions['reportType'] = 'UserAPI'
        if 'is_employee' in self._conditions:
            self._conditions['u_isemployee'] = self._conditions['is_employee']
        if 'user_status' in self._conditions:
            self._conditions['userstatus'] = self._conditions['user_status']
        self._args['endpoint'] = 'User'
        self._result = await self.query()
        return self._result

    async def curriculum_list(self):
        """curriculum_list.

        Get the Curriculum List.
        """
        self._args['endpoint'] = 'CurriculumList'
        self._result = await self.query()
        return self._result

    async def curriculum_assignments(self):
        """curriculum_assignments.

        Get the Curriculum Assignments.
        """
        if not 'delta_timestamp' in self._conditions:
            self._conditions['delta_timestamp'] = (datetime.now() - timedelta(days = 14)).strftime('%s')
        self._args['endpoint'] = 'CurriculumAssignments'
        self._result = await self.query()
        return self._result

    async def curriculum_assignments_removed(self):
        """curriculum_assignments_removed.

        Get the Curriculum Assignments Removed.
        """
        if not 'delta_timestamp' in self._conditions:
            self._conditions['delta_timestamp'] = (datetime.now() - timedelta(days = 14)).strftime('%s')
        self._args['endpoint'] = 'CurriculumAssignmentsRemoved'
        self._result = await self.query()
        return self._result

    async def course_list(self):
        """course_list.

        Get the Course List.
        """
        self._args['endpoint'] = 'CourseList'
        self._result = await self.query()
        return self._result

    async def course_enrollments(self):
        """course_enrollments.

        Get the Course Enrollments.
        """
        if not 'delta_timestamp' in self._conditions:
            self._conditions['delta_timestamp'] = (datetime.now() - timedelta(days = 14)).strftime('%s')
        self._args['endpoint'] = 'CourseEnrollments'
        self._result = await self.query()
        return self._result

    async def course_enrollments_removed(self):
        """course_enrollments_removed.

        Get the Course Enrollments Removed.
        """
        if not 'delta_timestamp' in self._conditions:
            self._conditions['delta_timestamp'] = (datetime.now() - timedelta(days = 14)).strftime('%s')
        self._args['endpoint'] = 'CourseEnrollmentsRemoved'
        self._result = await self.query()
        return self._result

    async def course_template_list(self):
        """course_template_list.

        Get the Course Template List.
        """
        self._args['endpoint'] = 'CourseTemplateList'
        self._result = await self.query()
        return self._result

    async def query(self):
        """
            Query.
            Basic Query of VenU API.
        """
        self._result = None
        # create URL
        self.url = self.build_url(
            self.url,
            args=self._args,
            queryparams=urlencode(self._conditions)
        )
        result = []
        try:
            result, error = await self.request(
                self.url,
                self.method
            )
            if error is not None:
                logging.error(f'VenU: Error: {error!s}')
            elif not result['Data']:
                raise DataNotFound('VenU: No data was found')
            else:
                self._result = result['Data']
                return self._result
        except (DataNotFound, NoDataFound) as err:
            raise DataNotFound('VenU: No data was found')
        except Exception as err:
            logging.error(f'VenU: Error: {err!s}')
