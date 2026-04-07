import asyncio
from querysource.parsers.influx import InfluxParser
from querysource.models import QueryModel

async def test_parser():
    print("Testing InfluxParser initialization and attribute access...")
    try:
        model = QueryModel(provider='influx', source='troc_task_execution')
        parser = InfluxParser(definition=model)
        
        # This was failing previously with AttributeError
        print("Accessing parser.bucket...")
        bucket = parser.bucket
        print(f"Bucket: {bucket}")
        
        print("Accessing parser.measurement...")
        measurement = parser.measurement
        print(f"Measurement: {measurement}")
        
        print("Setting attributes...")
        parser.bucket = "test_bucket"
        parser.measurement = "test_measurement"
        print(f"Updated Bucket: {parser.bucket}, Measurement: {parser.measurement}")
        
        print("SUCCESS! InfluxParser attributes are accessible.")
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(test_parser())
