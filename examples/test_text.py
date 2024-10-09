"""
Testing different string replacement methods
"""
from flashtext import KeywordProcessor
import re
import time
from datetime import datetime

class SafeDict(dict):
    """
    SafeDict.

    Allow to using partial format strings

    """

    def __missing__(self, key):
        """Missing method for SafeDict."""
        return "{" + key + "}"

replace = {
           "*": ["{fields}"],
           "current_date": ["{filterdate}", "{firstdate}", "{lastdate}"],
           ";": ["{where_cond}", "{filter}", "{and_cond}"]
}

replacement = {
           "fields": "*",
           "filterdate": "current_date",
           "firstdate": "current_date",
           "lastdate": "current_date",
           "where_cond": "",
           "and_cond": "",
           "filter": ""
}

str_replacement = {
           "{fields}": "*",
           "{filterdate}": "current_date",
           "{firstdate}": "current_date",
           "{lastdate}": "current_date",
           "{where_cond}": "",
           "{and_cond}": "",
           "{filter}": ""
}

rcfields = ["{fields}"]
rcdates = ["{filterdate}", "{firstdate}", "{lastdate}"]
rcempty = ["{where_cond}", "{and_cond}", "{filter}"]

sql = "SELECT {fields} FROM walmart.postpaid_apd_trend({filterdate}) {where_cond}"

print('First: Test Flashtext: ')
for key in range(100000):
    startTime = datetime.now()
    start = time.time()
    keyword_processor = KeywordProcessor(case_sensitive=True)
    keyword_processor.add_keywords_from_dict(replace)
    mid = time.time()
    newsql = keyword_processor.replace_keywords(sql)
    end = time.time()
print(newsql)
# print output
print("{0:.5f}".format(mid - start).ljust(9), '|', "{0:.5f}".format(end - start).ljust(9), '|',)
print("Generated in: %s" % (datetime.now() - startTime))

print('Second: Test SafeDict: ')
for key in range(100000):
    startTime = datetime.now()
    start = time.time()
    dc = SafeDict(**replacement)
    mid = time.time()
    newsql = sql.format_map(dc)
    end = time.time()
print(newsql)
# print output
print("{0:.5f}".format(mid - start).ljust(9), '|', "{0:.5f}".format(end - start).ljust(9), '|',)
print("Generated in: %s" % (datetime.now() - startTime))

print('Third: Using Regex: ')
for key in range(100000):
    startTime = datetime.now()
    start = time.time()
    rfc = r"|".join(rcfields)
    rfe = r"|".join(rcempty)
    rfd = r"|".join(rcdates)
    mid = time.time()
    newsql = re.sub(rfc, '*', sql)
    newsql = re.sub(rfe, '', newsql)
    newsql = re.sub(rfd, 'current_date', newsql)
    end = time.time()
print(newsql)
# print output
print("{0:.5f}".format(mid - start).ljust(9), '|', "{0:.5f}".format(end - start).ljust(9), '|',)
print("Generated in: %s" % (datetime.now() - startTime))

print('Fourth: String Replacement: ')
for key in range(100000):
    startTime = datetime.now()
    start = time.time()
    newsql = sql
    mid = time.time()
    for key, val in str_replacement.items():
        newsql = newsql.replace(key, val)
    end = time.time()
print(newsql)
# print output
print("{0:.5f}".format(mid - start).ljust(9), '|', "{0:.5f}".format(end - start).ljust(9), '|',)
print("Generated in: %s" % (datetime.now() - startTime))
