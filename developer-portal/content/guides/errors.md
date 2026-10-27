Errors are JSON objects returned with a 4xx or 5xx status:

```json
{
  "errorCode": "NOT_FOUND",
  "errorName": "ObjectNotFound",
  "errorInstanceId": "0d28ec01-c589-465d-a19e-c9e72c26ce21",
  "parameters": { "objectType": "Material", "primaryKey": "999999" }
}
```

* **errorCode** — category, maps to the HTTP status (`INVALID_ARGUMENT` 400, `UNAUTHORIZED` 401,
  `PERMISSION_DENIED` 403, `NOT_FOUND` 404, `CONFLICT` 409, `TOO_MANY_REQUESTS` 429, `INTERNAL` 500).
* **errorName** — the specific error; branch on this in code.
* **errorInstanceId** — unique per occurrence and recorded in the request log; quote it to support.
* **parameters** — details for debugging.
