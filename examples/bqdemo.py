from navconfig import config
from google.cloud import bigquery


credentials = config.get('GOOGLE_APPLICATION_CREDENTIALS')
client = bigquery.Client()

query = """
    SELECT store_id, SUM(sale_qty) AS sales, SUM(return_qty) AS returns
    FROM `unique-decker-385015.epson.sales`
    WHERE store_id != ''
    GROUP BY store_id
    ORDER BY sales
    DESC LIMIT 10
"""
results = client.query(query)

for row in results:
    title = row['store_id']
    sales = row['sales']
    returns = row['returns']
    print(f'{title:<20} | {sales} | {returns}')
