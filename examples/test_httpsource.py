import asyncio
from querysource.providers.sources import httpSource
from querysource.providers.sources.amazon import amazon
from querysource.providers.sources.upc import upc
from querysource.providers.sources.countries import countries
from querysource.providers.sources.gmaps import gmaps
from querysource.providers.sources.zipcodeapi import zipcodeapi

async def http_source(url):
    http = httpSource(
        url=url, use_proxy=False, rotate_ua=True
    )
    try:
        result = await http.query()
        print(http.last_execution())
    except Exception as er:
        print(er)
    assert result is not None
    bs = http.html() # BeautifulSoup parser.
    assert bs is not None
    # print(bs)

async def product_info(asin):
    product = amazon(
        asin=asin
    )
    try:
        result = await product.query()
        print(product.last_execution())
        print('PRODUCT INFO :: ', result)
    except Exception as er:
        print(er)

async def product_barcode(barcode):
    product = upc(type='product', barcode=barcode)
    try:
        result = await product.product()
        print(product.last_execution())
        print('PRODUCT INFO :: ', result)
    except Exception as er:
        print(er)

async def country_info():
    country = countries()
    all_countries = await country.all()
    print(all_countries)
    vzla = await country.country(country='Venezuela')
    print(vzla)
    usa = await country.code(code='US')
    print(usa)

async def check_route():
    data = dict(
        origin='Chicago,IL',
        destination='Los+Angeles,CA',
        waypoints='Joplin,MO|Oklahoma+City,OK',
        sensor='false'
    )
    gmap = gmaps(type='route')
    route = await gmap.route(data=data)
    print(route)

async def zipcode():
    params = {
        "type": "units",
        "zipcode": "33066",
        "units": "km",
        "api_key": "DemoOnly00a8bXzb30UtMePLSkDqCrBmAYL7h1PV35ibzG1k7fpTdtAS7pIr0KCY"
    }
    z = zipcodeapi(conditions=params)
    result = await z.query()
    print(result)
    print('-')
    result = await z.radius(zipcode=33066, radius=5)
    print(result)
    print('City Zips')
    result = await z.zipcode(city='Miami', state='FL')
    print(result)

# asyncio.run(http_source(url='https://www.google.com'))
asyncio.run(product_info(asin='B0773ZY26F'))
# asyncio.run(product_barcode(barcode='842776101129'))
# asyncio.run(country_info())
# asyncio.run(check_route())
#asyncio.run(zipcode())
