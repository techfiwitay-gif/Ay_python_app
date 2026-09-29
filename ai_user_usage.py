"""Owner-only Getreep AI usage reads and Excel export helpers."""

import io
import re
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import requests


class AiUserUsageError(Exception):
    """A reporting failure whose details must not include provider responses."""


def read_ai_user_usage(base, key, *, days, search, sort, limit, offset):
    headers = {"apikey": key, "Content-Type": "application/json"}
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = "Bearer " + key
    try:
        response = requests.post(
            base + "/rest/v1/rpc/admin_ai_user_usage",
            json={"p_days": days, "p_search": search, "p_sort": sort,
                  "p_limit": limit, "p_offset": offset},
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()
    except requests.RequestException as exc:
        raise AiUserUsageError("The private Getreep usage records could not be read.") from exc
    except ValueError as exc:
        raise AiUserUsageError("The Getreep usage response was invalid.") from exc
    if (not isinstance(result, dict) or not isinstance(result.get("accounts"), list)
            or not isinstance(result.get("matchingAccounts"), int)
            or not isinstance(result.get("totalAccounts"), int)
            or not isinstance(result.get("summary"), dict)):
        raise AiUserUsageError("The Getreep usage response was invalid.")
    return result


def _cell(value, row, column, style=0):
    reference = f"{chr(65 + column)}{row}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{reference}" s="{style}"><v>{value}</v></c>'
    text = "" if value is None else str(value)
    # XML 1.0 does not allow most control characters. Keep account text safe
    # and as inline strings so values beginning with = cannot become formulas.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return f'<c r="{reference}" s="{style}" t="inlineStr"><is><t>{escape(text, {"\"": "&quot;", "\'": "&apos;"})}</t></is></c>'


def _row(values, number, style=0):
    return f'<row r="{number}">' + "".join(
        _cell(value, number, index, style) for index, value in enumerate(values)
    ) + "</row>"


def ai_usage_workbook(accounts, days, search):
    rows = [
        _row(["Getreep AI usage by Apple account"], 1, 1),
        _row([f"Last {days} days", "Search", search or "All accounts"], 2),
        _row(["Name", "Email", "Account ID", "Input tokens", "Output tokens",
              "Total tokens", "Requests", "Estimated cost (USD)",
              "Unpriced requests", "Last AI use (UTC)", "Joined (UTC)"], 4, 1),
    ]
    for index, account in enumerate(accounts, 5):
        input_tokens = account.get("inputTokens", 0)
        output_tokens = account.get("outputTokens", 0)
        rows.append(_row([
            account.get("name"), account.get("email"), account.get("id"),
            input_tokens, output_tokens, input_tokens + output_tokens,
            account.get("requestCount", 0), account.get("estimatedCostMicros", 0) / 1_000_000,
            account.get("unpricedRequests", 0), account.get("lastUsedAt"),
            account.get("createdAt"),
        ], index))
    end = max(4, len(accounts) + 4)
    worksheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                 f'<dimension ref="A1:K{end}"/><sheetViews><sheetView workbookViewId="0"/>'
                 '</sheetViews><sheetFormatPr defaultRowHeight="15"/>'
                 '<cols><col min="1" max="2" width="24" customWidth="1"/>'
                 '<col min="3" max="3" width="40" customWidth="1"/>'
                 '<col min="4" max="9" width="20" customWidth="1"/>'
                 '<col min="10" max="11" width="28" customWidth="1"/></cols>'
                 f'<sheetData>{"".join(rows)}</sheetData><autoFilter ref="A4:K{end}"/>'
                 '</worksheet>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
              '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>'
              '<fills count="3"><fill><patternFill patternType="none"/></fill>'
              '<fill><patternFill patternType="gray125"/></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FF154C42"/>'
              '<bgColor indexed="64"/></patternFill></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
              '</cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" '
              'borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" '
              'borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/>'
              '</cellStyles></styleSheet>')
    parts = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="AI usage" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": worksheet,
        "xl/styles.xml": styles,
    }
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=6) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return output.getvalue()
