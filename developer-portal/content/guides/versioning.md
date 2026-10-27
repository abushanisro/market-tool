The API version is part of the path: `/api/v2`. Within a version, changes are additive only — new endpoints, new
optional parameters and new response fields. Clients must ignore unknown fields.

Every response carries `X-Emithran-Api-Version: 2.0`.

## Preview endpoints

Endpoints marked **Preview** may still change. They must be called with `preview=true`; otherwise they return
`400 INVALID_ARGUMENT / ApiFeaturePreviewUsageOnly`. Currently in preview:
[Get Material Alternatives](/docs/material-intelligence/materials/get-material-alternatives).
