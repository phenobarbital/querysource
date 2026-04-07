import asyncio
from querysource.queries import QS

async def test_execution():
    print("Initializing QS...")
    qs = QS(slug='troc_task_execution')
    try:
        print("Building provider...")
        await qs.build_provider()
        print("Provider initialized successfully")
        
        # Test query build parsing
        print("Dry run parsing query...")
        result, error = await qs.dry_run()
        print("Parsed Query:")
        print(result)
        if error:
            print("Error:", error)
    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        print("Closing QS...")
        await qs.close()

if __name__ == '__main__':
    asyncio.run(test_execution())
