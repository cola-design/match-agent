# API search limits 1.4.1

Ordinary search defaults to 200 and accepts limit=1..500. Pagination accepts page_size=1..500. An explicit limit=50 still returns at most 50. Ordinary responses remain arrays; paginated responses remain objects with data and has_more. Filters and authentication are unchanged.

The public app.py deliberately contains no private MongoDB URI or API-key defaults. Keep product_contract.py and the server-local .env next to app.py. The existing MONGODB_URI and MONGODB_API_KEY must be present in .env or process environment. Do not overwrite .env with an empty example. If the previous application used hardcoded defaults only, save those settings privately in .env before replacement.

Download the raw app.py in this directory, replace the damaged server app.py, run python -m py_compile app.py, and restart with the existing service command.

Verification: eight local HTTP contract checks passed using in-memory records and a stubbed MongoClient; no company database was contacted. No live server deployment or load test was performed.
