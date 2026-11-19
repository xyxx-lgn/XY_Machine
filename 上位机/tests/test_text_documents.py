import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from pypdf import PdfWriter

from app.generation import GenerationError
from app.stroke_fonts import FontPackage, WritingOptions
from app.text_documents import BOOLEAN_SETTINGS, CHOICE_SETTINGS, NUMBER_SETTINGS, font_coverage, import_text, read_manuscript, save_manuscript, validate_manuscript


ROOT = Path(__file__).resolve().parent.parent


def manuscript():
    settings = {name: low for name, (low, high) in NUMBER_SETTINGS.items()}
    settings.update({name: False for name in BOOLEAN_SETTINGS})
    settings.update({name: sorted(values)[0] for name, values in CHOICE_SETTINGS.items()})
    settings.update(font="Microsoft YaHei", stroke_font="", pen_up=2, pen_down=-.4)
    return {"format": "xy-manuscript", "schema": 1, "text": "中文 English 123\n\n第二段", "paper": {"width": 210, "height": 297}, "page": 2, "settings": settings}


class TextDocumentTests(unittest.TestCase):
    def setUp(self):
        folder = ROOT / ".runtime" / "text-document-unit"
        folder.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=folder)
        self.folder = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def docx(self, body, extras=None):
        path = self.folder / "测试.docx"
        xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>'
        with ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", xml)
            for name, value in (extras or {}).items():
                archive.writestr(name, value)
        return path

    def test_text_encodings_and_blank_paragraphs(self):
        for encoding in ("utf-8", "utf-8-sig", "utf-16", "gb18030"):
            with self.subTest(encoding=encoding):
                path = self.folder / "正文.txt"
                path.write_bytes("中文\r\n\r\nEnglish 123\t结束".encode(encoding))
                self.assertEqual(import_text(path).text, "中文\n\nEnglish 123\t结束")

    def test_limits_and_binary_text_fail_without_truncation(self):
        path = self.folder / "正文.txt"
        for text in ("字" * 20001, "字\n" * 1001, "a\0b", "  \n"):
            path.write_text(text, encoding="utf-8")
            with self.subTest(text=text[:20]), self.assertRaises(GenerationError):
                import_text(path)

    def test_txt_page_break_is_explicit(self):
        path = self.folder / "正文.txt"
        path.write_text("第一页\f第二页", encoding="utf-8")
        result = import_text(path)
        self.assertEqual(result.text, "第一页\n第二页")
        self.assertIn("分页符", "".join(result.notes))

    def test_docx_runs_hyperlinks_tabs_and_empty_paragraphs(self):
        path = self.docx('<w:p><w:r><w:t>中文</w:t></w:r><w:hyperlink><w:r><w:t>链接</w:t></w:r></w:hyperlink><w:r><w:tab/><w:t>ABC</w:t><w:br/><w:t>下一行</w:t></w:r></w:p><w:p/><w:p><w:r><w:t>末段</w:t></w:r></w:p>')
        self.assertEqual(import_text(path).text, "中文链接\tABC\n下一行\n\n末段")

    def test_docx_resolves_inherited_alignment_and_page_margins(self):
        styles = ('<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                  '<w:style w:type="paragraph" w:styleId="TitleBase"><w:pPr><w:jc w:val="center"/></w:pPr></w:style>'
                  '<w:style w:type="paragraph" w:styleId="Title"><w:basedOn w:val="TitleBase"/></w:style></w:styles>')
        title = '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>标题</w:t><w:br/><w:t>副标题</w:t></w:r></w:p>'
        body = '<w:p><w:pPr><w:pStyle w:val="Title"/><w:jc w:val="left"/></w:pPr><w:r><w:t>正文</w:t></w:r></w:p>'
        sign = '<w:p><w:pPr><w:jc w:val="right"/></w:pPr><w:r><w:t>落款</w:t></w:r></w:p>'
        section = '<w:sectPr><w:pgMar w:left="1800" w:right="1800" w:top="1440" w:bottom="1440"/></w:sectPr>'
        result = import_text(self.docx(title + body + sign + section, {"word/styles.xml": styles}))
        self.assertEqual(result.text, "标题\n副标题\n正文\n落款")
        self.assertEqual(result.paragraph_alignments, ("center", "center", "left", "right"))
        self.assertEqual(result.page_margins, (31.75, 31.75, 25.4, 25.4))

    def test_docx_font_sizes_inherit_and_use_the_main_size_per_paragraph(self):
        styles = ('<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                  '<w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>'
                  '<w:style w:type="paragraph" w:styleId="Base"><w:rPr><w:sz w:val="52"/></w:rPr></w:style>'
                  '<w:style w:type="paragraph" w:styleId="Title"><w:basedOn w:val="Base"/></w:style>'
                  '<w:style w:type="character" w:styleId="Large"><w:rPr><w:sz w:val="145"/></w:rPr></w:style></w:styles>')
        title = '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>四字标题</w:t><w:br/><w:t>附标题</w:t></w:r><w:r><w:rPr><w:rStyle w:val="Large"/></w:rPr><w:t>甲</w:t></w:r></w:p>'
        body = '<w:p><w:r><w:rPr><w:sz w:val="36"/></w:rPr><w:t>大段正文</w:t></w:r></w:p>'
        default = '<w:p><w:r><w:t>默认文字</w:t></w:r></w:p>'
        unsupported = '<w:p><w:r><w:rPr><w:sz w:val="200"/></w:rPr><w:t>特大字</w:t></w:r></w:p>'
        result = import_text(self.docx(title + body + default + unsupported, {"word/styles.xml": styles}))
        self.assertEqual(result.paragraph_font_sizes, (26, 26, 18, 11, None))

    def test_pdf_without_text_and_encrypted_pdf_are_explained(self):
        path = self.folder / "文档.pdf"
        writer = PdfWriter()
        writer.add_blank_page(595, 842)
        writer.write(path)
        with self.assertRaisesRegex(GenerationError, "OCR"):
            import_text(path)
        writer.encrypt("test-password")
        writer.write(path)
        with self.assertRaisesRegex(GenerationError, "PDF已加密"):
            import_text(path)

    def test_docx_tables_and_unhandled_content_warn(self):
        path = self.docx('<w:p><w:pPr><w:numPr/></w:pPr><w:r><w:t>正文</w:t><w:drawing/><w:footnoteReference/></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>甲</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>乙</w:t></w:r></w:p></w:tc></w:tr></w:tbl>', {"word/header1.xml": "header"})
        result = import_text(path)
        self.assertEqual(result.text, "正文\n甲\t乙")
        for term in ("表格", "图片", "自动编号", "页眉"):
            self.assertIn(term, "".join(result.notes))

    def test_docx_changes_and_textbox_not_duplicated(self):
        path = self.docx('<w:p><w:del><w:r><w:delText>旧</w:delText></w:r></w:del><w:ins><w:r><w:t>新</w:t></w:r></w:ins><w:r><w:drawing><w:txbxContent><w:p><w:r><w:t>浮动文字</w:t></w:r></w:p></w:txbxContent></w:drawing></w:r></w:p>')
        result = import_text(path)
        self.assertEqual(result.text, "新")
        self.assertIn("修订", "".join(result.notes))

    def test_bad_docx_and_unsupported_extensions(self):
        for name in ("旧文档.doc", "损坏.docx"):
            path = self.folder / name
            path.write_bytes(b"not a document")
            with self.subTest(name=name), self.assertRaises(GenerationError):
                import_text(path)

    def test_empty_docx_and_broken_xml(self):
        with self.assertRaises(GenerationError):
            import_text(self.docx('<w:p/>'))
        with self.assertRaises(GenerationError):
            import_text(self.docx('<w:p>'))

    def test_coverage_personal_missing_visible_even_with_fallback(self):
        package = FontPackage("测试", {"甲": {"advance": 1, "variants": [{"strokes": [[(.1, .1), (.8, .8)]]}]}})
        result = font_coverage("甲甲阿阿阿 A，\n", WritingOptions(package=package, fallback=True))
        self.assertEqual(result.own, ["甲"])
        self.assertEqual(result.recording, ["阿"])
        self.assertEqual(result.fallback, ["阿"])
        self.assertEqual(result.counts["阿"], 3)
        self.assertEqual(result.unavailable, [])
        result = font_coverage("甲阿 A，", WritingOptions(package=package, fallback=False))
        self.assertEqual(result.unavailable, ["阿"])
        self.assertEqual(result.recording, ["阿"])

    def test_coverage_unavailable_and_order(self):
        result = font_coverage("😀阿😀🦄", WritingOptions())
        self.assertEqual(result.unavailable, ["😀", "🦄"])
        self.assertEqual(result.recording, ["😀", "🦄"])

    def test_unsupported_space_not_silently_supported(self):
        result = font_coverage("阿\u2002阿", WritingOptions())
        self.assertEqual(result.unavailable, ["\u2002"])
        self.assertEqual(result.recording, [])

    def test_manuscript_roundtrip_unicode_and_z(self):
        data = manuscript()
        target = save_manuscript(data, self.folder / "我的文章")
        self.assertEqual(target.suffix, ".xytext")
        self.assertEqual(read_manuscript(target), data)
        self.assertFalse(target.with_suffix(".xytext.tmp").exists())

    def test_manuscript_restores_mixed_sizes_and_option_to_override(self):
        data = manuscript()
        data.update(paragraph_font_sizes=[24, None, 12], use_document_font_sizes=False)
        self.assertEqual(read_manuscript(save_manuscript(data, self.folder / "我的文稿")), data)
        for invalid in ([12], [24, False, 12], [24, None, 200]):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(GenerationError, "字号"):
                validate_manuscript(dict(data, paragraph_font_sizes=invalid))

    def test_invalid_save_does_not_overwrite(self):
        path = save_manuscript(manuscript(), self.folder / "我的文章.xytext")
        original = path.read_bytes()
        invalid = manuscript()
        invalid["settings"]["pen_down"] = 3
        with self.assertRaises(GenerationError):
            save_manuscript(invalid, path)
        self.assertEqual(path.read_bytes(), original)
        with self.assertRaises(GenerationError):
            save_manuscript(manuscript(), self.folder / "错扩展名.json")

    def test_invalid_manuscripts_rejected(self):
        mutations = [lambda d: d.update(schema=True), lambda d: d.update(schema=2), lambda d: d.update(page=0),
                     lambda d: d["settings"].update(font_size=999), lambda d: d["settings"].update(natural_seed=1.5),
                     lambda d: d["settings"].update(bold="false"), lambda d: d["settings"].update(width=float("nan")),
                     lambda d: d["settings"].update(mode="unknown"), lambda d: d["settings"].pop("font"),
                     lambda d: d["paper"].update(width=-1)]
        for mutate in mutations:
            data = manuscript()
            mutate(data)
            with self.subTest(data=str(data)[:50]), self.assertRaises(GenerationError):
                validate_manuscript(data)

    def test_broken_manuscript(self):
        path = self.folder / "损坏.xytext"
        path.write_text('{"format":', encoding="utf-8")
        with self.assertRaises(GenerationError):
            read_manuscript(path)


if __name__ == "__main__":
    unittest.main()
