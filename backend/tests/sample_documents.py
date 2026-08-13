from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject


def make_minimal_pdf() -> bytes:
    """生成可被标准 PDF 阅读器解析的一页 ASCII PDF。"""
    stream = b"BT /F1 12 Tf 72 720 Td (Travel reimbursement policy) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")

    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode())
    output.extend(f"startxref\n{xref_offset}\n%%EOF\n".encode())
    return bytes(output)


def make_image_backed_pdf(page_texts: tuple[str | None, ...]) -> bytes:
    """生成每页都含真实 Image XObject、并可选原生文字层的 PDF。"""
    output = BytesIO()
    writer = PdfWriter()

    for page_text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        resources = DictionaryObject()
        commands = [b"q 100 0 0 100 72 600 cm /Im0 Do Q"]

        image_stream = DecodedStreamObject()
        image_stream.set_data(b"\xff\xff\xff")
        image_stream.update(
            {
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(1),
                NameObject("/Height"): NumberObject(1),
                NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                NameObject("/BitsPerComponent"): NumberObject(8),
            }
        )
        image_ref = writer._add_object(image_stream)
        resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im0"): image_ref})

        if page_text is not None:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            font_ref = writer._add_object(font)
            resources[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): font_ref})
            commands.append(f"BT /F1 12 Tf 72 720 Td ({page_text}) Tj ET".encode("ascii"))

        content = DecodedStreamObject()
        content.set_data(b"\n".join(commands))
        page[NameObject("/Resources")] = resources
        page[NameObject("/Contents")] = writer._add_object(content)

    writer.write(output)
    return output.getvalue()


def make_minimal_docx() -> bytes:
    """生成包含必要 OPC/Word 条目的最小 DOCX。"""
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
            'package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
            'package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
            "Travel reimbursement policy</w:t></w:r></w:p></w:body></w:document>",
        )
    return output.getvalue()


def make_generic_zip() -> bytes:
    """生成可打开但不包含 OOXML 文档骨架的普通 ZIP。"""
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("notes.txt", "not a Word document")
    return output.getvalue()
