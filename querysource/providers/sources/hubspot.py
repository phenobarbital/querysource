import logging
from typing import Any
from hubspot import HubSpot
from urllib.parse import urlencode
from asyncdb.exceptions import ProviderError, NoDataFound
from ...providers.sources import restSource


class hubspot(restSource):
    """
      HubSpot CRM
        API for integration with CRM HubSpot
    """
    base_url: str = 'https://api.hubapi.com'
    method: str = 'get'
    timeout: int = 30
    type: str = None
    auth_type: str = 'api_key'
    auth: dict = {
        "API-Key": None
    }

    #def __init__(self, definition=None, params: dict = {}, **kwargs):
    #    super(hubspot, self).__init__(definition, params, **kwargs)
    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:

        try:
            self.type = definition.params['type']
        except (ValueError, AttributeError):
            self.type = None

        if 'type' in self._conditions:
            self.type = self._conditions['type']
            del self._conditions['type']

        if 'api_key' in self._conditions:
            api_token = self._conditions['api_key']
            del self._conditions['api_key']
        else:
            api_token = self._env.get('HUBSPOT_API_KEY')
            if not api_token:
                try:
                    api_token = definition.params['api_key']
                except (ValueError, AttributeError):
                    raise ValueError("HubSpot: Missing API Key")
        self._headers['Authorization'] = f'Bearer {api_token}'

        # self._conditions = params
        self._args = kwargs

        if self.type == 'campaigns':
            self.url = self.base_url + '/email/public/v1/campaigns/by-id'
        elif self.type == 'campaign':
            try:
                self._args['campaign_id'] = self._conditions['campaign_id']
                del self._conditions['campaign_id']
            except (KeyError, AttributeError):
                raise ValueError("HubSpot: Missing Email Campaign ID")
            self.url = self.base_url + '/email/public/v1/campaigns/{campaign_id}'
        elif self.type == 'active_campaigns':
            self.url = self.base_url + '/email/public/v1/campaigns'
        elif self.type == 'marketing_email_stats':
            self._conditions['limit'] = 100
            self.url = self.base_url + '/marketing-emails/v1/emails/with-statistics'
        elif self.type == 'engagements':
            self._conditions['limit'] = 250
            self.url = self.base_url + '/engagements/v1/engagements/paged'
        elif self.type == 'active_engagements':
            self._conditions['limit'] = 250
            self.url = self.base_url + '/engagements/v1/engagements/recent/modified'
        elif self.type == 'engagement':
            try:
                self._args['engagement_id'] = self._conditions['engagement_id']
                del self._conditions['engagement_id']
            except (KeyError, AttributeError):
                raise ValueError("HubSpot: Missing Engagement ID")
            self.url = self.base_url + '/engagements/v1/engagements/{engagement_id}'
        elif self.type == 'email_events':
            self.url = self.base_url + '/email/public/v1/events'
        elif self.type == 'companies':
            self.url = self.base_url + '/companies/v2/companies/paged'
        elif self.type == 'company':
            self.url = self.base_url + '/companies/v2/companies/{companyId}'
            try:
                self._args['companyId'] = self._conditions['companyId']
                del self._conditions['companyId']
            except (KeyError, AttributeError):
                raise ValueError("HubSpot: Missing Company ID")
        elif self.type == 'contacts':
            self.url = self.base_url + '/contacts/v1/lists/all/contacts/all'
            self._conditions['count'] = 100
            self._conditions['showListMemberships'] = True
        elif self.type == 'contact':
            self.url = self.base_url + '/contacts/v1/contact/vid/{vid}/profile'
            self._conditions['showListMemberships'] = True
            try:
                self._args['vid'] = self._conditions['vid']
                del self._conditions['vid']
            except (KeyError, AttributeError):
                raise ValueError("HubSpot: Missing User VID ID")
        elif self.type == 'company_contacts':
            self.url = self.base_url + '/companies/v2/companies/{companyId}/contacts'
            self._conditions['count'] = 100
            try:
                self._args['companyId'] = self._conditions['companyId']
                del self._conditions['companyId']
            except (KeyError, AttributeError):
                raise ValueError("HubSpot: Missing Company ID")
        elif self.type == 'contact_by_email':
            self.url = self.base_url + '/contacts/v1/contact/email/{email}/profile'
            self.type = 'contact'
        elif self.type == 'companies_list':
            self.url = self.base_url + '/crm/v3/objects/companies'
            self._conditions['limit'] = 100
        elif self.type == 'contacts_list':
            self.url = self.base_url + '/crm/v3/objects/contacts'
            self._conditions['limit'] = 100

    async def marketing_email_stats(self):
        """marketing_email_stats.

        Get the statistics for all marketing emails.
        """
        self.method = 'get'
        self._conditions['limit'] = 100
        self.url = self.base_url + '/marketing-emails/v1/emails/with-statistics'
        self.type = 'marketing_email_stats'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def social_media_channels(self):
        """Social media Channels.

        Get Publishing channels.
        """
        self.method = 'get'
        self.url = self.base_url + '/broadcast/v1/channels/setting/publish/current'
        self.type = 'social_media'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def broadcasts(self):
        self.method = 'get'
        self.url = self.base_url + '/broadcast/v1/broadcasts'
        self.type = 'social_media'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def engagements(self):
        """engagements.

        get all engagements in an account.
        """
        self.method = 'get'
        self._conditions['limit'] = 250
        self.url = self.base_url + '/engagements/v1/engagements/paged'
        self.type = 'engagements'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def active_engagements(self):
        """engagements.

        get all engagements in an account.
        """
        self.method = 'get'
        self._conditions['limit'] = 250
        self.url = self.base_url + '/engagements/v1/engagements/recent/modified'
        self.type = 'engagements'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def engagement(self):
        """engagement.

        get an engagement (a task or activity) for a CRM record in HubSpot.
        """
        self.method = 'get'
        try:
            if 'engagement_id' not in self._args:
                self._args['engagement_id'] = self._conditions['engagement_id']
                del self._conditions['engagement_id']
        except (KeyError, AttributeError):
            raise ValueError("HubSpot: Missing Engagement ID")
        self.url = self.base_url + '/engagements/v1/engagements/{engagement_id}'
        self.type = 'engagement'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def owner(self):
        """campaigns.

        Get information about a Campaign
        """
        self.method = 'get'
        self.url = self.base_url + '/owners/v2/owners/{ownerId}'
        self.type = 'owner'
        try:
            if 'ownerId' not in self._args:
                self._args['ownerId'] = self._conditions['ownerId']
                del self._conditions['ownerId']
        except (KeyError, AttributeError):
            raise ValueError("HubSpot: Missing Owner ID")
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def campaign(self):
        """campaigns.

        Get information about a Campaign
        """
        self.method = 'get'
        self.url = self.base_url + '/email/public/v1/campaigns/{campaign_id}'
        self.type = 'campaign'
        try:
            if 'campaign_id' not in self._args:
                self._args['campaign_id'] = self._conditions['campaign_id']
                del self._conditions['campaign_id']
        except (KeyError, AttributeError):
            raise ValueError("HubSpot: Missing Email Campaign ID")
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def campaigns(self):
        """campaigns.

        Get all the campaigns in a given portal.
        """
        self.method = 'get'
        self.url = self.base_url + '/email/public/v1/campaigns/by-id'
        self.type = 'active_campaigns'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def email_events(self):
        """email_events.

        query the event log for events.
        """
        self.method = 'get'
        self.url = self.base_url + '/email/public/v1/events'
        self.type = 'email_events'
        try:
            self._conditions['campaignId'] = self._conditions['campaign_id']
            del self._conditions['campaign_id']
        except (KeyError, AttributeError):
            raise ValueError("HubSpot: Missing Email Campaign ID")
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def active_campaigns(self):
        """active_campaigns.

        Get all the active campaigns in a given portal.
        """
        self.method = 'get'
        self.url = self.base_url + '/email/public/v1/campaigns'
        self.type = 'active_campaigns'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def companies(self):
        """companies.

        Get all companies in the HubSpot account.
        """
        self.method = 'get'
        self.url = self.base_url + '/companies/v2/companies/paged'
        self.type = 'companies'
        self._conditions['limit'] = 100
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def companies_list(self):
        """companies_list.

        Get all companies in the HubSpot account (api v3).
        """
        self.method = 'get'
        self.url = self.base_url + '/crm/v3/objects/companies'
        self.type = 'companies_list'
        self._conditions['limit'] = 100
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def company(self):
        """company.

        Get all information about a Company
        """
        self.method = 'get'
        self.url = self.base_url + '/companies/v2/companies/{companyId}'
        self.type = 'company'
        try:
            if 'company' not in self._args:
                self._args['companyId'] = self._conditions['companyId']
                del self._conditions['companyId']
        except (KeyError, AttributeError):
            raise ValueError("HubSpot: Missing Company ID")
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def events(self):
        """events.

        Get all events on a given portal.
        """
        self.method = 'get'
        self.url = self.base_url + '/reports/v2/events'
        self.type = 'events'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def event(self):
        """event.

        Given an event, returns a specific event definition in a given portal.
        """
        self.method = 'get'
        self.url = self.base_url + '/reports/v2/events/{event_id}'
        self.type = 'event'
        try:
            self._args['event_id'] = self._conditions['event_id']
            del self._conditions['event_id']
        except (KeyError, AttributeError):
            raise ValueError("HubSpot: Missing Event ID")
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def contacts(self):
        """user contacts.

        Get all contacts in the HubSpot account.
        """
        self.method = 'get'
        self.url = self.base_url + '/contacts/v1/lists/all/contacts/all'
        self.type = 'contacts'
        self._conditions['count'] = 100
        self._conditions['showListMemberships'] = True
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def contacts_list(self):
        """user contacts.

        Get all contacts in the HubSpot account (v3 api).
        """
        self.method = 'get'
        self.url = self.base_url + '/crm/v3/objects/contacts'
        self.type = 'contacts_list'
        self._conditions['limit'] = 100
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def contact_by_email(self):
        """user contact.

        Given an User Id (vid), returns the contact.
        """
        self.method = 'get'
        self.url = self.base_url + '/contacts/v1/contact/email/{email}/profile'
        self.type = 'contact'
        try:
            self._args['email'] = self._conditions['email']
            del self._conditions['email']
        except (KeyError, AttributeError):
            raise ValueError("HubSpot: Missing User Email for Search")
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def contact(self):
        """user contact.

        Given an User Id (vid), returns the contact.
        """
        self.method = 'get'
        self.url = self.base_url + '/contacts/v1/contact/vid/{vid}/profile'
        self.type = 'contact'
        self._conditions['showListMemberships'] = True
        try:
            self._args['vid'] = self._conditions['vid']
            del self._conditions['vid']
        except (KeyError, AttributeError):
            raise ValueError("HubSpot: Missing User VID ID")
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def company_contacts(self):
        """user contacts.

        Get all contacts in the HubSpot account.
        """
        self.method = 'get'
        self.url = self.base_url + '/companies/v2/companies/{companyId}/contacts'
        self.type = 'company_contacts'
        self._conditions['count'] = 100
        try:
            self._args['companyId'] = self._conditions['companyId']
            del self._conditions['companyId']
        except (KeyError, AttributeError):
            raise ValueError("HubSpot: Missing Company ID")
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def deals(self):
        """deals.

        Get all deals in the HubSpot account (v3 api).
        """
        self.method = 'get'
        self.url = self.base_url + '/crm/v3/objects/deals'
        self.type = 'deals'
        self._conditions['limit'] = 100
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def leads(self):
        """leads.

        Get all leads in the HubSpot account (v3 api).
        """
        self.method = 'get'
        self.url = self.base_url + '/crm/v3/objects/leads'
        self.type = 'leads'
        self._conditions['limit'] = 100
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def get_next_result(
        self,
        result,
        event: str = '',
        more:str = 'hasMore',
        offset_name: str = 'offset',
        offset_url: str = 'offset'
    ):
        r = result[event]
        next = True
        page = 1
        offset = result[offset_name]
        more_results = result[more]
        while next == True:
            if more_results is False:
                break
            url = self.build_url(
                self.url, queryparams=urlencode({offset_url: offset})
            )
            page = page + 1
            print('Fetching page %s' % page)
            try:
                res, error = await self.request(url)
                if error:
                    print(error)
                    next = False
                    break
                data = res[event]
                offset = res[offset_name]
                more_results = res[more]
                if len(data) > 0:
                    r = r + data
                else:
                    next = False
                    break
            except Exception as err:
                print(err)
                next = False
        print('::  Returning Results')
        return r

    async def get_next_result_v2(
        self,
        result,
        event: str = '',
        more:str = 'has-more',
        offset_name: str = 'offset',
        offset_url: str = 'offset'
    ):
        r = result[event]
        next = True
        offset = result[offset_name] + 1
        while next == True:
            url = self.build_url(
                self.url, queryparams=urlencode({offset_url: offset})
            )
            offset = offset + 1
            print('Fetching page %s' % offset)
            try:
                res, error = await self.request(url)
                if error:
                    print(error)
                    next = False
                    break
                data = res[event]
                if len(data) > 0:
                    r = r + data
                else:
                    next = False
                    break
            except Exception as err:
                print(err)
                next = False
        print('::  Returning Results')
        return r

    async def get_next_result_v3(
        self,
        result,
        event: str = ''
    ):
        r = result[event]
        if 'paging' not in result:
            return r
        next = True
        paging = result['paging']
        page = 1
        try:
            offset = paging['next']['after']
        except (ValueError, KeyError):
            next = False
        while next == True:
            url = self.build_url(
                self.url, queryparams=urlencode({"after": offset})
            )
            print('Fetching page %s' % page)
            try:
                res, error = await self.request(url)
                if error:
                    print(error)
                    next = False
                    break
                data = res[event]
                try:
                    paging = res['paging']
                    offset = paging['next']['after']
                except (ValueError, KeyError):
                    next = False
                    break
                page = page + 1
                if len(data) > 0:
                    r = r + data
                else:
                    next = False
                    break
            except Exception as err:
                print(err)
                next = False
        print('::  Returning Results')
        print('result...', r)
        return r

    async def query(self):
        """Query.

        Basic Query of HubSpot API.
        """
        self._result = None
        # create URL
        self.url = self.build_url(
            self.url,
            args=self._args,
            queryparams=urlencode(self._conditions)
        )
        try:
            result, error = await self.request(
                self.url, self.method
            )
            #self._result = result
            #return self._result
            if not result:
                raise NoDataFound('HubSpot: No data was found')
            elif 'status' in result and result['status'] == 'error':
                raise ProviderError(result['message'])
            if error:
                print(err)
                raise ProviderError(str(error))
            if self.type in ['campaigns', 'active_campaigns']:
                self._result = await self.get_next_result(result, 'campaigns')
            elif self.type in ['email_events']:
                self._result = await self.get_next_result(result, 'events')
            elif self.type in ['engagements']:
                self._result = await self.get_next_result(result, 'results')
            elif self.type in ['companies']:
                self._result = await self.get_next_result(result, self.type, more='has-more')
            elif self.type == 'company_contacts':
                self._result = await self.get_next_result(
                    result,
                    'contacts',
                    more='hasMore',
                    offset_name='vidOffset',
                    offset_url='vidOffset'
                )
            elif self.type == 'contacts':
                self._result = await self.get_next_result(
                    result,
                    self.type,
                    more='has-more',
                    offset_name='vid-offset',
                    offset_url='vidOffset'
                )
            elif self.type == 'marketing_email_stats':
                try:
                    self._result = await self.get_next_result_v2(result, 'objects')
                except Exception as err:
                    logging.error(err)
            elif self.type in ['companies_list', 'contacts_list', 'deals', 'leads']:
                # version 3 of API
                try:
                    self._result = await self.get_next_result_v3(result, 'results')
                except Exception as err:
                    logging.error(err)
            else:
                self._result = result
        except NoDataFound as err:
            print("HUBSPOT NO DATA FOUND: === ", err)
            raise
            self._result = None
        except ProviderError as err:
            print("HUBSPOT PROVIDER ERROR: === ", err)
            raise
        except Exception as err:
            print("HUBSPOT ERROR: === ", err)
            self._result = None
            raise
        finally:
            return self._result
