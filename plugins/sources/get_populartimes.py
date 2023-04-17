import logging
import copy
from querysource.exceptions import *
from asyncdb.exceptions import NoDataFound
import populartimes
import livepopulartimes
import numpy as np
from .rest import restSource

def resume_populartimes(data):
    hours = np.array([data[i]['data'] for i in range(len(data))]).T
    #visits_by_hour = []{'{}h'.format(str(i)): np.sum(hours[i]) for i in range(len(hours))}
    visits_by_hour = [{"hour": i,f"{i}h": np.sum(hours[i])} for i in range(len(hours))]
    visits = [{'day': data[i]['name'].lower(),'{}_total'.format(data[i]['name']).lower() :np.sum(data[i]['data']),'{}_avg'.format(data[i]['name']).lower():np.mean(data[i]['data'])} for i in  range(len(data))]
    hours = {x['name']:x['data'] for x in data}
    result = {"visits": visits,"visits_by_hour": visits_by_hour, "hours": hours}
    # for key,val in cols.items():
    #     result[key] = val
    return result


class get_populartimes(restSource):
    """
      PopularTimes Google API
        Get populartimes from api
    """
    _url = ''
    _place_id = ''

    def __init__(self, definition=None, params={}, loop=None, env=None):
        super(get_populartimes, self).__init__(definition, params, loop, env)
        conditions = copy.deepcopy(params)
        try:
            self.type = definition.params['type']
        except (ValueError, KeyError):
            self.type = 'get_id'

        try:
            self.api_key = conditions['api_key']
            del conditions['api_key']
        except (ValueError, KeyError):
            try:
                self.api_key = definition.params['api_key']
            except (ValueError, KeyError):
                raise "PopularTimes: No API KEY defined"

        # Place ID
        try:
            self._place_id = conditions['place_id']
        except (ValueError, KeyError):
            raise

    async def connect(self):
        result = None
        if self.type == 'get_id':
            try:
                result = livepopulartimes.get_populartimes_by_PlaceID(self.api_key, self._place_id)
            except Exception as err:
                print('ERROR: ', err)
                return False
        try:
            if result:
                try:
                    pt = result['populartimes']
                    popular = resume_populartimes(pt)
                    result = {
                        **result,
                        **popular
                    }
                except Exception as err:
                    print(err)
                except KeyError:
                    # store doesnt have PopularTimes
                    pass
        except (ValueError, TypeError):
            raise NoDataFound("PopularTimes Error: Empty Result")
        finally:
            return result
