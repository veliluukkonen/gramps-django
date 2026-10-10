"""Tests for apps.core.html (StyledText to HTML)."""

from django.test import SimpleTestCase, TestCase

from apps.core.html import get_note_html, process_spaces, sanitize, styledtext_to_html
from apps.core.models import Note, Person


def styled(string, *tags):
    return {"string": string, "tags": [{"name": n, "value": v, "ranges": r} for n, v, r in tags]}


class StyledTextToHtmlTests(SimpleTestCase):
    def test_empty(self):
        self.assertEqual(styledtext_to_html({"string": "", "tags": []}, 0), "")
        self.assertEqual(styledtext_to_html({}, 0), "")
        self.assertEqual(styledtext_to_html(None, 0), "")

    def test_plain_paragraph(self):
        html = styledtext_to_html({"string": "Hello world", "tags": []}, 0)
        self.assertEqual(html, '<div class="grampsstylednote">\n<p>Hello world</p>\n</div>')

    def test_bold_italic_underline(self):
        html = styledtext_to_html(styled("Hello world", ("bold", None, [[0, 5]]), ("italic", None, [[6, 11]])), 0)
        self.assertIn("<strong>Hello</strong> <em>world</em>", html)
        html = styledtext_to_html(styled("Hello", ("underline", None, [[0, 5]])), 0)
        self.assertIn('<span style="text-decoration:underline;">Hello</span>', html)
        html = styledtext_to_html(styled("Hello", ("superscript", None, [[0, 1]])), 0)
        self.assertIn("<sup>H</sup>ello", html)

    def test_tag_names_as_gramps_type_dicts(self):
        text = {"string": "Hello", "tags": [{"name": {"_class": "StyledTextTagType", "string": "bold", "value": 0}, "value": None, "ranges": [[0, 5]]}]}
        self.assertIn("<strong>Hello</strong>", styledtext_to_html(text, 0))
        text = {"string": "Hello", "tags": [{"name": {"_class": "StyledTextTagType", "string": "", "value": 1}, "value": None, "ranges": [[0, 5]]}]}
        self.assertIn("<em>Hello</em>", styledtext_to_html(text, 0))

    def test_overlapping_tags_are_nested_properly(self):
        # bold 0-8, italic 4-12 on "aaaabbbbcccc"
        html = styledtext_to_html(styled("aaaabbbbcccc", ("bold", None, [[0, 8]]), ("italic", None, [[4, 12]])), 0)
        # Like Gramps: all open tags are closed and reopened at every boundary
        self.assertIn("<strong>aaaa</strong><em><strong>bbbb</strong></em><em>cccc</em>", html)

    def test_font_styles(self):
        html = styledtext_to_html(
            styled("abc", ("fontcolor", "#ff0000", [[0, 1]]), ("highlight", "#00ff00", [[1, 2]]),
                   ("fontface", "Times New Roman", [[2, 3]])),
            0,
        )
        self.assertIn('<span style="color:#ff0000;">a</span>', html)
        self.assertIn('<span style="background-color:#00ff00;">b</span>', html)
        self.assertIn("font-family:&#x27;Times New Roman&#x27;;", html)
        html = styledtext_to_html(styled("abc", ("fontsize", 14, [[0, 3]])), 0)
        self.assertIn('<span style="font-size:14px;">abc</span>', html)

    def test_links(self):
        html = styledtext_to_html(styled("see here", ("link", "https://example.com/?a=1&b=2", [[4, 8]])), 0)
        self.assertIn('see <a href="https://example.com/?a=1&amp;b=2">here</a>', html)
        # javascript: links are not allowed; the text is just underlined
        html = styledtext_to_html(styled("click", ("link", "javascript:alert(1)", [[0, 5]])), 0)
        self.assertNotIn("javascript", html)
        self.assertIn('<span style="text-decoration:underline;">click</span>', html)
        # gramps:// links without a link format are underlined
        html = styledtext_to_html(styled("person", ("link", "gramps://Person/handle/abc", [[0, 6]])), 0)
        self.assertIn('<span style="text-decoration:underline;">person</span>', html)

    def test_escaping(self):
        html = styledtext_to_html({"string": "<script>alert('x')</script> & co", "tags": []}, 0)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt; &amp; co", html)
        # escaped text inside bold must not be split by tags
        html = styledtext_to_html(styled("a<b", ("bold", None, [[0, 3]])), 0)
        self.assertIn("<strong>a&lt;b</strong>", html)

    def test_paragraphs_and_line_breaks(self):
        html = styledtext_to_html({"string": "line one\nline two\n\nsecond para", "tags": []}, 0)
        self.assertIn("<p>line one<br />\nline two</p>", html)
        self.assertIn("<p>second para</p>", html)
        self.assertEqual(html.count("<p>"), 2)
        # bold across a line break is closed and reopened
        html = styledtext_to_html(styled("ab\ncd", ("bold", None, [[0, 5]])), 0)
        self.assertIn("<strong>ab</strong><br />\n<strong>cd</strong>", html)
        # trailing blank line produces an empty paragraph
        html = styledtext_to_html({"string": "text\n", "tags": []}, 0)
        self.assertEqual(html.count("<p>"), 2)
        self.assertIn("<p>&nbsp;</p>", html)

    def test_preformatted(self):
        html = styledtext_to_html({"string": "  two  spaces", "tags": []}, 1)
        self.assertIn("<p>&nbsp; two &nbsp;spaces</p>", html)
        flowed = styledtext_to_html({"string": "  two  spaces", "tags": []}, 0)
        self.assertIn("<p>  two  spaces</p>", flowed)
        self.assertEqual(process_spaces("a <b>  c</b>", 1), ("a <b> &nbsp;c</b>", 2))
        self.assertEqual(process_spaces("<b>x</b>", 0), ("<b>x</b>", 1))

    def test_html_code_notes_are_sanitized(self):
        text = {"string": '<p onclick="x()">Hi <b>there</b> <script>alert(1)</script><a href="javascript:x" title="t">l</a></p>', "tags": []}
        html = styledtext_to_html(text, 0, contains_html=True)
        self.assertIn("<p>Hi <b>there</b> ", html)
        self.assertNotIn("onclick", html)
        self.assertNotIn("alert", html)
        self.assertIn('<a title="t">l</a>', html)
        # The sanitizer drops the class attribute, like bleach does in gramps-web-api
        self.assertTrue(html.startswith("<div><p>Hi"))

    def test_sanitize(self):
        self.assertEqual(sanitize('<span style="color:red;position:absolute">x</span>'), '<span style="color:red;">x</span>')
        self.assertEqual(sanitize("<iframe src='x'>y</iframe>"), "y")
        self.assertEqual(sanitize("a<br>b"), "a<br />b")
        self.assertEqual(sanitize('<a href="http://x.y/">l</a>'), '<a href="http://x.y/">l</a>')


class GrampsLinkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.person = Person.objects.create(handle="h1", gramps_id="I0001")
        cls.note = Note.objects.create(
            handle="n1", gramps_id="N0001", type="General", format=0,
            text=styled("Matti and others", ("link", "gramps://Person/handle/h1", [[0, 5]])),
        )
        cls.html_note = Note.objects.create(
            handle="n2", gramps_id="N0002", type="HTML Code", format=0,
            text={"string": "<b>bold</b><script>x</script>", "tags": []},
        )

    def test_gramps_link_resolution(self):
        html = get_note_html(self.note, link_format="/person/{gramps_id}")
        self.assertIn('<a href="/person/I0001">Matti</a>', html)
        html = get_note_html(self.note, link_format="/{obj_class}/{handle}")
        self.assertIn('<a href="/person/h1">Matti</a>', html)
        html = get_note_html(self.note)
        self.assertIn('<span style="text-decoration:underline;">Matti</span>', html)

    def test_gramps_link_by_gramps_id_and_missing_target(self):
        text = styled("Matti", ("link", "gramps://Person/gramps_id/I0001", [[0, 5]]))
        self.assertIn('<a href="/person/I0001">Matti</a>', styledtext_to_html(text, 0, link_format="/{obj_class}/{gramps_id}"))
        text = styled("Nobody", ("link", "gramps://Person/gramps_id/I9999", [[0, 6]]))
        self.assertIn('<span style="text-decoration:underline;">Nobody</span>', styledtext_to_html(text, 0, link_format="/{obj_class}/{gramps_id}"))

    def test_html_code_note(self):
        html = get_note_html(self.html_note)
        self.assertIn("<b>bold</b>", html)
        self.assertNotIn("script", html)
