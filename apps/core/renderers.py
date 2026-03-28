import json

from rest_framework.renderers import JSONRenderer


class GrampsJSONRenderer(JSONRenderer):
    """JSON renderer that sorts keys alphabetically to match Gramps Web API."""

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return b""
        renderer_context = renderer_context or {}
        renderer_context["indent"] = getattr(self, "indent", None)
        ret = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return ret.encode("utf-8")
