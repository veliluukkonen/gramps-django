"""
StyledText to HTML conversion for notes (``formats=html``).

Port of ``get_note_html`` from gramps-web-api, which in turn uses the
Gramps ``HtmlBackend`` / ``DocBackend.add_markup_from_styled`` to turn a
StyledText into markup.  The note text is stored as JSON::

    {"string": "...", "tags": [{"name": "bold", "value": None,
                                "ranges": [[start, end], ...]}, ...]}

Tag names: bold, italic, underline, fontface, fontsize, fontcolor,
highlight, superscript, link, strikethrough, subscript.  Text is always
HTML-escaped; "HTML Code" notes are passed through a whitelist sanitizer.
"""

import html as _html
import re
from html.parser import HTMLParser

ALLOWED_TAGS = {
    "a", "abbr", "acronym", "b", "blockquote", "code", "em", "i", "li", "ol",
    "strong", "ul", "span", "p", "br", "div", "sup", "sub", "s", "u",
}

ALLOWED_ATTRIBUTES = {
    "a": {"href", "title", "style"},
    "abbr": {"title", "style"},
    "acronym": {"title", "style"},
    "p": {"style"},
    "div": {"style"},
    "span": {"style"},
}

ALLOWED_CSS_PROPERTIES = {
    "color", "background-color", "font-family", "font-weight", "font-size",
    "font-style", "text-decoration",
}

ALLOWED_URL_SCHEMES = ("http", "https", "mailto", "ftp")

VOID_TAGS = {"br"}

# Gramps StyledTextTagType value -> xml name (for tags stored as integers)
TAG_NAMES = [
    "bold", "italic", "underline", "fontface", "fontsize", "fontcolor",
    "highlight", "superscript", "link", "strikethrough", "subscript",
]

BOOL_TAG_MARKUP = {
    "bold": ("<strong>", "</strong>"),
    "italic": ("<em>", "</em>"),
    "underline": ('<span style="text-decoration:underline;">', "</span>"),
    "superscript": ("<sup>", "</sup>"),
    "strikethrough": ("<s>", "</s>"),
    "subscript": ("<sub>", "</sub>"),
}

STYLE_PROPERTY = {
    "fontcolor": "color:%s;",
    "highlight": "background-color:%s;",
    "fontface": "font-family:'%s';",
    "fontsize": "font-size:%spx;",
}

UNDERLINE = BOOL_TAG_MARKUP["underline"]

_GRAMPS_CLASSES = {
    "person": "Person", "family": "Family", "event": "Event", "place": "Place",
    "source": "Source", "citation": "Citation", "repository": "Repository",
    "media": "Media", "note": "Note",
}


def escape(text):
    """Escape text for HTML (including quotes, so it is attribute safe)."""
    return _html.escape(str(text), quote=True)


def _tag_name(name):
    """Normalise a tag name given as a string, a GrampsType dict or an int."""
    if isinstance(name, dict):
        value = name.get("string")
        if not value and isinstance(name.get("value"), int) and 0 <= name["value"] < len(TAG_NAMES):
            value = TAG_NAMES[name["value"]]
        name = value or ""
    elif isinstance(name, int) and not isinstance(name, bool):
        name = TAG_NAMES[name] if 0 <= name < len(TAG_NAMES) else ""
    return str(name or "").strip().lower()


def _safe_href(url):
    """Return the url if its scheme is allowed (or it is relative), else None."""
    url = str(url or "").strip()
    if not url:
        return None
    lowered = url.lower().replace("\n", "").replace("\r", "").replace("\t", "")
    match = re.match(r"^([a-z][a-z0-9+.-]*):", lowered)
    if match and match.group(1) not in ALLOWED_URL_SCHEMES:
        return None
    return url


def _style_value(value):
    """Keep a CSS value simple: no semicolons, braces or url()."""
    value = str(value or "")
    value = re.sub(r"[;{}<>\"]", "", value)
    if re.search(r"url\s*\(|expression\s*\(", value, re.I):
        return ""
    return value.replace("'", "\\'") if "'" in value else value


def _lookup_object(obj_class, prop, value):
    """Resolve a gramps:// link target to ``(gramps_id, handle)`` or None."""
    from .models import (  # imported lazily so the module stays importable without DB
        Citation, Event, Family, MediaObject, Note, Person, Place, Repository, Source,
    )

    models = {
        "Person": Person, "Family": Family, "Event": Event, "Place": Place,
        "Source": Source, "Citation": Citation, "Repository": Repository,
        "Media": MediaObject, "Note": Note,
    }
    model = models.get(obj_class)
    if model is None:
        return None
    if prop == "handle":
        obj = model.objects.filter(pk=value).only("handle", "gramps_id").first()
    elif prop == "gramps_id":
        obj = model.objects.filter(gramps_id=value).only("handle", "gramps_id").first()
    else:
        return None
    if obj is None:
        return None
    return obj.gramps_id or "", obj.handle


def format_link(value, link_format=None):
    """Return the (open, close) markup for a link tag value.

    ``gramps://Person/handle/abc`` links are turned into a link built from
    ``link_format`` (e.g. ``"/{obj_class}/{gramps_id}"``) if given and the
    target exists; otherwise they are rendered underlined, like Gramps does.
    """
    value = str(value or "")
    if value.startswith("gramps://"):
        if link_format is None:
            return UNDERLINE
        parts = value[len("gramps://"):].split("/", 2)
        if len(parts) != 3:
            return UNDERLINE
        obj_class, prop, target = parts
        obj_class = _GRAMPS_CLASSES.get(obj_class.lower(), obj_class)
        if prop not in ("handle", "gramps_id"):
            return UNDERLINE
        found = _lookup_object(obj_class, prop, target)
        if found is None:
            return UNDERLINE
        gramps_id, handle = found
        try:
            href = link_format.format(obj_class=obj_class.lower(), gramps_id=gramps_id, handle=handle)
        except (KeyError, IndexError, ValueError):
            return UNDERLINE
    else:
        href = _safe_href(value)
        if href is None:
            return UNDERLINE
    return ('<a href="%s">' % escape(href), "</a>")


def markup_for_tag(name, value, link_format=None):
    """Return ``(open, close)`` markup for a styled text tag, or None."""
    if name in BOOL_TAG_MARKUP:
        return BOOL_TAG_MARKUP[name]
    if name == "link":
        return format_link(value, link_format)
    if name in STYLE_PROPERTY:
        if name == "fontsize":
            try:
                value = int(float(value))
            except (TypeError, ValueError):
                return None
        value = _style_value(value)
        if not value and name != "fontsize":
            return None
        return ('<span style="%s">' % escape(STYLE_PROPERTY[name] % value), "</span>")
    return None


def add_markup_from_styled(text, tags, split="\n", link_format=None, escape_text=True):
    """Port of ``DocBackend.add_markup_from_styled``.

    Tags are opened/closed so that they never overlap, and all open tags are
    closed before each ``split`` string and reopened after it, so the output
    can be split on it safely.
    """
    text = str(text)
    escape_func = escape if escape_text else (lambda value: str(value))
    first, last = 0, 1
    tagspos = {}
    for tag in tags or []:
        if not isinstance(tag, dict):
            continue
        markup = markup_for_tag(_tag_name(tag.get("name")), tag.get("value"), link_format)
        if markup is None:
            continue
        for rng in tag.get("ranges") or []:
            try:
                start, end = int(rng[0]), int(rng[1])
            except (TypeError, ValueError, IndexError):
                continue
            if end <= start:
                continue
            tagspos.setdefault(start, []).append((markup, first))
            tagspos[end] = [(markup, last)] + tagspos.get(end, [])

    keylist = sorted(pos for pos in tagspos if pos <= len(text))
    opentags = []
    output = []
    start = 0

    def write_text(segment_end):
        nonlocal start
        if segment_end <= start:
            return
        if split:
            splitpos = text[start:segment_end].find(split)
            while splitpos != -1:
                output.append(escape_func(text[start:start + splitpos]))
                for opentag in reversed(opentags):
                    output.append(opentag[1])
                output.append(escape_func(split))
                for opentag in opentags:
                    output.append(opentag[0])
                start = start + splitpos + len(split)
                splitpos = text[start:segment_end].find(split)
        output.append(escape_func(text[start:segment_end]))

    for pos in keylist:
        write_text(pos)
        for markup, kind in tagspos[pos]:
            for opentag in reversed(opentags):
                output.append(opentag[1])
            if kind == first:
                opentags = [markup] + opentags
            else:
                opentags = [tag for tag in opentags if tag != markup]
            for opentag in opentags:
                output.append(opentag[0])
        start = pos

    write_text(len(text))
    for opentag in reversed(opentags):
        output.append(opentag[1])
    return "".join(output)


def process_spaces(intext, note_format):
    """Port of ``libhtmlbackend.process_spaces``.

    For preformatted notes leading and repeated spaces become alternating
    ``&nbsp;`` and spaces.  Returns ``(text, significant character count)``.
    """
    NORMAL, SPACE, NBSP, XML, SPACEHOLD = 1, 2, 3, 4, 5
    sigcount = 0
    state = NORMAL
    out = []
    if note_format == 1:
        for char in intext:
            if state == NORMAL:
                if char == " ":
                    if sigcount == 0:
                        state = NBSP
                        out.append("&nbsp;")
                    else:
                        state = SPACEHOLD
                elif char == "<":
                    state = XML
                    out.append(char)
                else:
                    sigcount += 1
                    out.append(char)
            elif state == SPACE:
                if char == " ":
                    state = NBSP
                    out.append("&nbsp;")
                elif char == "<":
                    state = XML
                    out.append(char)
                else:
                    sigcount += 1
                    state = NORMAL
                    out.append(char)
            elif state == NBSP:
                if char == " ":
                    state = SPACE
                elif char == "<":
                    state = XML
                else:
                    sigcount += 1
                    state = NORMAL
                out.append(char)
            elif state == XML:
                if char == ">":
                    state = NORMAL
                out.append(char)
            elif state == SPACEHOLD:
                if char == " ":
                    out.append(" &nbsp;")
                    state = NORMAL
                elif char == "<":
                    out.append(" " + char)
                    state = XML
                else:
                    out.append(" " + char)
                    sigcount += 1
                    state = NORMAL
    else:
        for char in intext:
            if char == "<" and state == NORMAL:
                state = XML
            elif char == ">" and state == XML:
                state = NORMAL
            elif state != XML:
                sigcount += 1
            out.append(char)
    return "".join(out), sigcount


def _paragraphs(markup, note_format):
    """Split marked-up text into ``<p>`` paragraphs like the Gramps backend."""
    paragraphs = []
    linelist = []
    linenb = 1
    sigcount = 0
    for line in markup.split("\n"):
        line, sigcount = process_spaces(line, note_format)
        if sigcount == 0:
            # An empty paragraph is rendered as a non-breaking space.
            if linenb == 1:
                linelist.append("&nbsp;")
            paragraphs.append(linelist)
            linelist = []
            linenb = 1
        else:
            if linenb > 1:
                linelist[-1] += "<br />"
            linelist.append(line)
            linenb += 1
    if linenb > 1:
        paragraphs.append(linelist)
    if sigcount == 0:
        paragraphs.append(["&nbsp;"])
    return ["<p>%s</p>" % "\n".join(lines) for lines in paragraphs]


class _Sanitizer(HTMLParser):
    """Whitelist sanitizer for "HTML Code" notes (replacement for bleach)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skip_depth = 0  # inside <script>/<style>: drop content

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip_depth += 1
            return
        if self.skip_depth or tag not in ALLOWED_TAGS:
            return
        rendered = []
        for name, value in attrs:
            name = name.lower()
            if name not in ALLOWED_ATTRIBUTES.get(tag, set()):
                continue
            if name == "href":
                value = _safe_href(value)
                if value is None:
                    continue
            elif name == "style":
                value = self._clean_style(value)
                if not value:
                    continue
            rendered.append(' %s="%s"' % (name, escape(value or "")))
        self.out.append("<%s%s%s>" % (tag, "".join(rendered), " /" if tag in VOID_TAGS else ""))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        self.out.append("</%s>" % tag)

    def handle_data(self, data):
        if not self.skip_depth:
            self.out.append(escape(data))

    @staticmethod
    def _clean_style(style):
        kept = []
        for declaration in str(style or "").split(";"):
            if ":" not in declaration:
                continue
            prop, value = declaration.split(":", 1)
            prop = prop.strip().lower()
            value = _style_value(value.strip())
            if prop in ALLOWED_CSS_PROPERTIES and value:
                kept.append("%s:%s;" % (prop, value))
        return "".join(kept)


def sanitize(html):
    """Keep only whitelisted tags/attributes/CSS; escape everything else."""
    parser = _Sanitizer()
    parser.feed(str(html or ""))
    parser.close()
    return "".join(parser.out)


def styledtext_to_html(text, note_format=0, contains_html=False, link_format=None):
    """Convert a note's StyledText JSON to HTML.

    ``text`` is ``{"string": ..., "tags": [...]}`` (a plain string is also
    accepted), ``note_format`` is 0 for flowed and 1 for preformatted text.
    With ``contains_html`` (note type "HTML Code") the string is treated as
    HTML and sanitized instead of escaped.  ``link_format`` builds hrefs
    for ``gramps://`` links, e.g. ``"/{obj_class}/{gramps_id}"``.
    """
    if isinstance(text, dict):
        string = text.get("string") or ""
        tags = text.get("tags") or []
    else:
        string = text or ""
        tags = []
    string = str(string)
    if not string:
        return ""

    if contains_html:
        markup = add_markup_from_styled(string, tags, split="\n", link_format=link_format, escape_text=False)
        return sanitize('<div class="grampsstylednote">%s</div>' % markup)

    markup = add_markup_from_styled(string, tags, split="\n", link_format=link_format)
    body = "\n".join(_paragraphs(markup, int(note_format or 0)))
    return '<div class="grampsstylednote">\n%s\n</div>' % body


def get_note_html(note, link_format=None):
    """Return the HTML for a Note model instance."""
    return styledtext_to_html(
        note.text,
        note_format=note.format,
        contains_html=(note.type == "HTML Code"),
        link_format=link_format,
    )
