---
id: F002
query_id: Q002
type: read
intent: reference implementation to mirror
executed_at: 2026-09-30T14:27:56Z
parent_id: null
depth: 0
---
# F002 — SharepointSource implementation
## Summary
`SharepointSource(ThreadSource)` (332 lines) downloads ONE Excel/CSV file via Microsoft Graph using app-only `ClientSecretCredential` (azure-identity aio) + `GraphServiceClient`. Two addressing modes: (a) full `url` resolved via Graph `/shares` (`_encode_share_url` → `u!<b64url>`), (b) `tenant_name`+`site`+`directory`+`filename` → site id → drives → `root:/<path>:`. Download via `@microsoft.graph.downloadUrl` with httpx (600 s timeout). Parsing in `_parse_file_content` (extension → read_excel/read_csv, sheet_name, pd_args, stringified headers). Masks resolved on directory/filename. Patches `platform.version` for kiota User-Agent.
## Citations
- path: `querysource/queries/multi/sources/sharepoint.py`
  lines: 20-76
  symbol: `SharepointSource` (class + config docstring)
- path: `querysource/queries/multi/sources/sharepoint.py`
  lines: 78-125
  symbol: `SharepointSource.__init__`
  excerpt: |
    creds = options.get('credentials', {})
    self._client_id = self.resolve_credential('client_id', creds.get('client_id', 'SHAREPOINT_APP_ID'))
- path: `querysource/queries/multi/sources/sharepoint.py`
  lines: 127-140
  symbol: `SharepointSource._encode_share_url`
- path: `querysource/queries/multi/sources/sharepoint.py`
  lines: 142-179
  symbol: `SharepointSource._parse_file_content`
- path: `querysource/queries/multi/sources/sharepoint.py`
  lines: 181-332
  symbol: `SharepointSource.fetch`
  excerpt: |
    credential = ClientSecretCredential(self._tenant_id, self._client_id, self._client_secret)
    client = GraphServiceClient(credentials=credential, scopes=["https://graph.microsoft.com/.default"])
    ...
    item = await client.drives.by_drive_id(drive_id).items.by_drive_item_id(f"root:/{file_path}:").get()
    download_url = item.additional_data.get('@microsoft.graph.downloadUrl')
## Notes
Only the drive-resolution block (lines 237-310) is SharePoint-specific; auth, /shares, download and parsing are drive-agnostic and apply unchanged to a OneDrive drive.
