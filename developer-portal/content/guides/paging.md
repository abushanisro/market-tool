List and search endpoints are paged.

* `pageSize` — 1 to 1000 (default 100). Out of range → `InvalidPageSize`.
* `pageToken` — omit on the first request; then send the `nextPageToken` from the previous response.
* `nextPageToken` is absent/`null` on the last page. `totalCount` is returned where it is cheap to compute.

Tokens are opaque; do not construct them. A malformed token returns `InvalidPageToken`.

```python
token = None
while True:
    page = requests.get(url, params={"pageSize": 500, "pageToken": token}, headers=h).json()
    handle(page["data"])
    token = page.get("nextPageToken")
    if not token:
        break
```
