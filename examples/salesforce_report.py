
## https://polestareducation.lightning.force.com/lightning/r/Report/00O5G000008ycq8UAA/view?queryScope=userFolders

from io import StringIO
import pandas as pd
import requests
from simple_salesforce import Salesforce
from querysource.conf import (
    SALESFORCE_INSTANCE,
    SALESFORCE_TOKEN,
    SALESFORCE_USERNAME,
    SALESFORCE_PASSWORD
)

# Input Salesforce credentials:
sf = Salesforce(
    username=SALESFORCE_USERNAME,
    password=SALESFORCE_PASSWORD,
    security_token=SALESFORCE_TOKEN
)  # See below for help with finding token

# Basic report URL structure:
orgParams = f"{SALESFORCE_INSTANCE}/"  # you can see this in your Salesforce URL
exportParams = '?isdtp=p1&export=1&enc=UTF-8&xf=csv'

reportId = '00O5G000008ycq8UAA'
sf_report_loc = "{0}/{1}?isdtp=p1&export=1&enc=UTF-8&xf=csv".format(SALESFORCE_INSTANCE, reportId)


# sf_report_loc = f"{SALESFORCE_INSTANCE}/services/data/v39.0/analytics/reports/{reportId}/describe"
# print(sf_report_loc)

report_list = f"{SALESFORCE_INSTANCE}/services/data/v39.0/analytics/reportTypes"


report_list = f"{SALESFORCE_INSTANCE}/services/data/v39.0/analytics/reports"
# report_list = f"{SALESFORCE_INSTANCE}/services/data/v35.0/analytics/dashboards"
print(report_list)


# Downloading the report:
reportReq = requests.get(
    sf_report_loc,
    headers=sf.headers,
    cookies={'sid': sf.session_id},
    timeout=60
)
reportData = reportReq.content.decode('utf-8')
print('REPORT DATA > ', reportData)
print('TYPE ', type(reportData))
reportDf = pd.read_csv(StringIO(reportData))

print('DF > ', reportDf)
