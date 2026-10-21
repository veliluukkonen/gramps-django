"""Tests for the extend parameter."""

from django.test import TestCase

from apps.analysis.tests import FixtureMixin
from apps.core.extend import get_extended_attributes


class ExtendOrderTests(FixtureMixin, TestCase):
    def test_events_follow_ref_list_order(self):
        # Clients pair extended.events with event_ref_list by index, so a
        # reordered ref list must not come back in database order.
        person = self.people["matti"]
        person.event_ref_list = list(reversed(person.event_ref_list))
        handles = [ref["ref"] for ref in person.event_ref_list]
        self.assertGreater(len(handles), 1)
        extended = get_extended_attributes(person, {"event_ref_list"}, {})
        self.assertEqual([e["handle"] for e in extended["events"]], handles)

    def test_missing_event_keeps_indices_aligned(self):
        person = self.people["matti"]
        handles = [ref["ref"] for ref in person.event_ref_list]
        person.event_ref_list = [{"ref": "MISSING"}] + list(person.event_ref_list)
        extended = get_extended_attributes(person, {"event_ref_list"}, {})
        self.assertEqual(extended["events"][0], {})
        self.assertEqual([e["handle"] for e in extended["events"][1:]], handles)
