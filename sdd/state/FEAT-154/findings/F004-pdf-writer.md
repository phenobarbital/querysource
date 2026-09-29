---
id: F004
query_id: Q005
type: read
intent: weasyprint usage
executed_at: 2026-09-29T15:46:30Z
parent_id: null
depth: 0
---
# F004 — PDFWriter uses weasyprint.HTML only inside get_response, synchronously

## Summary
`PDFWriter(ReportWriter)` references `HTML` only in `get_response` (L48-60): `HTML(string=result).write_pdf(output)`. The import can be moved into the method with no behaviour change. Note: the render is a synchronous, CPU-bound call inside an `async def` (blocks the event loop) — adjacent concern, not the subject of this request.

## Citations
- path: `querysource/outputs/writers/pdf.py`
  lines: 10-16
  symbol: `PDFWriter`
- path: `querysource/outputs/writers/pdf.py`
  lines: 48-54
  symbol: `PDFWriter.get_response`
  excerpt: |
    async def get_response(self) -> web.StreamResponse:
        output = BytesIO()
        result = await self.render_content()
        response = await self.response(self.response_type)
        HTML(string=result).write_pdf(output)
