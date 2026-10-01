from functions import _search_api

rows = _search_api("iphone 13", limit=10)
print(f"got {len(rows)} listings")
for r in rows:
    print(f"  NPR {r['price']:>9,}  {r['title'][:60]}  →  {r['url']}")