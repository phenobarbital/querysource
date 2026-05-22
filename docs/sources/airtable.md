# AirtableSource

## Overview

`AirtableSource` is a MultiQuery ThreadSource that reads records from an Airtable
base and returns them as a pandas DataFrame. Each row corresponds to one Airtable
record; columns are taken from the record's `fields` object. It plugs into any
MultiQuery pipeline the same way as `TableSource`, `S3Source`, or `SharepointSource`.
Write operations (create, update, delete) are not supported in this release — see
[spec §1 Non-Goals](../../sdd/specs/multi-threadsource-airtable.spec.md#1-motivation--business-requirements).

---

## Configuration

`AirtableSource` accepts two shape variants for the `source` block.

### URL form

```yaml
source:
  type: AirtableSource
  url: "https://airtable.com/appXXXXXXXXXXXXXX/tblYYYYYYYYYYYYYY/viwZZZZZZZZZZZZZZ"
```

The URL is parsed into `base_id`, `table`, and (optionally) `view` automatically.
This is the recommended form — it matches what you copy from the Airtable web UI.

### Explicit form

```yaml
source:
  type: AirtableSource
  base_id: appXXXXXXXXXXXXXX
  table: tblYYYYYYYYYYYYYY
  view: viwZZZZZZZZZZZZZZ   # optional
```

### Optional API-side filters

All three optional fields are forwarded directly to the Airtable List Records API:

```yaml
source:
  type: AirtableSource
  url: "https://airtable.com/appXXX/tblYYY"
  filter_by_formula: "NOT({Status} = 'Done')"
  max_records: 500
  page_size: 50   # default: 100; Airtable max: 100
```

---

## Auth modes

Auth resolution follows this precedence (highest to lowest):

1. **Session OAuth** — if `navigator_session` is installed and the current user's
   session contains an `airtable` key (written by the OAuth callback), the stored
   `access_token` is used. This is per-user and automatically refreshed once on 401.

2. **Personal Access Token (PAT)** — the `AIRTABLE_ACCESS_TOKEN` environment variable
   is used as a fallback. This token is **global / server-wide** (shared across all
   users and all requests). See [spec §1 Non-Goals] for the security implications.

3. **Error** — if neither is available, `AirtableSource.fetch()` raises `RuntimeError`.

> **Note:** PAT-only operation works without enabling the OAuth flow or setting
> `QS_AIRTABLE_OAUTH_ENABLED`. Just set `AIRTABLE_ACCESS_TOKEN` in your environment.

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `AIRTABLE_ACCESS_TOKEN` | For PAT auth | — | Personal Access Token (server-wide) |
| `AIRTABLE_CLIENT_ID` | For OAuth | — | Airtable OAuth app client ID |
| `AIRTABLE_CLIENT_SECRET` | For OAuth | — | Airtable OAuth app client secret |
| `AIRTABLE_REDIRECT_URI` | For OAuth | `http://localhost:5000/api/v1/qs/integrations/airtable/callback` | Must match Airtable app registration exactly |
| `AIRTABLE_BASE_ID` | No | — | Default base ID (can be overridden per-source) |
| `QS_AIRTABLE_OAUTH_ENABLED` | No | `false` | Set `true` to register `/connect` and `/callback` routes |

---

## Enabling the OAuth flow

OAuth is optional. Skip this section if you only need PAT auth.

1. **Register an OAuth app** on [Airtable's developer console](https://airtable.com/create/oauth).

2. **Set the redirect URI** in the Airtable app settings to:
   ```
   https://<your-host>/api/v1/qs/integrations/airtable/callback
   ```

3. **Configure environment variables**:
   ```bash
   AIRTABLE_CLIENT_ID=your-client-id
   AIRTABLE_CLIENT_SECRET=your-client-secret
   AIRTABLE_REDIRECT_URI=https://<your-host>/api/v1/qs/integrations/airtable/callback
   QS_AIRTABLE_OAUTH_ENABLED=true
   ```

4. **Restart QuerySource**. Verify the route is live:
   ```bash
   curl -I https://<your-host>/api/v1/qs/integrations/airtable/connect
   # expect: HTTP 200
   ```

5. **Users visit `/connect`**, click "Connect to Airtable", and complete the Airtable
   consent page. On success, tokens are stored in their `navigator_session` under the
   key `airtable` and the source will use OAuth on subsequent requests.

---

## Token storage and reconnect UX

OAuth tokens are stored in `navigator_session['airtable']` as:
`{access_token, refresh_token, expires_at, scope, token_type}`.

When an access token expires, `AirtableInterface` automatically attempts a one-time
token refresh using `refresh_token`. If the refresh also fails (e.g., the refresh
token has been revoked), `AirtableSource.fetch()` raises `AirtableReauthRequired`.

Callers should catch `AirtableReauthRequired` and redirect the user back to
`/api/v1/qs/integrations/airtable/connect` to re-authorize.

---

## Known limitations

The following are documented in [spec §7 Known Risks](../../sdd/specs/multi-threadsource-airtable.spec.md):

- **No streaming** — all records are fetched into memory before the DataFrame is
  returned. Tables larger than ~100 MB may cause memory pressure.
- **429 Not retried** — Airtable rate-limit responses raise `RuntimeError` immediately.
  Add retry logic at the pipeline level if needed.
- **No incremental pagination** — the source always fetches from offset 0.
- **Linked records and attachments** — returned as raw JSON objects, not expanded.
  Field-type normalization is a future enhancement (see spec §8 Open Questions).
- **No write support** — `create_records`, `update_records`, and `delete_records`
  are stubs that raise `NotImplementedError`. This is intentional per spec §1 Non-Goals.

---

## Example: MultiQuery pipeline with AirtableSource

```yaml
# Fetch active Airtable records and join with a local DB table.
query:
  type: multi
  sources:
    - name: airtable_contacts
      type: AirtableSource
      url: "https://airtable.com/appXXXXXXXXXXXXXX/tblYYYYYYYYYYYYYY"
      filter_by_formula: "NOT({Status} = 'Inactive')"
      max_records: 1000

    - name: crm_accounts
      type: TableSource
      table: crm.accounts
      schema: public

  transformations:
    - type: Merge
      left: airtable_contacts
      right: crm_accounts
      on: email
      how: left

  output:
    format: json
```

Run the pipeline:

```bash
curl -X POST https://<your-host>/api/v3/queries/my-airtable-pipeline \
  -H "Content-Type: application/json"
```
