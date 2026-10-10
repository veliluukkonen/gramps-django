"""URL routes for the analysis endpoints (timeline, relations, DNA)."""

from django.urls import path

from . import views

urlpatterns = [
    path("people/<str:handle>/timeline", views.PersonTimelineView.as_view(), name="person-timeline"),
    path("people/<str:handle>/timeline/", views.PersonTimelineView.as_view()),
    path("families/<str:handle>/timeline", views.FamilyTimelineView.as_view(), name="family-timeline"),
    path("families/<str:handle>/timeline/", views.FamilyTimelineView.as_view()),
    path("timelines/people/", views.TimelinePeopleView.as_view(), name="timeline-people"),
    path("timelines/people", views.TimelinePeopleView.as_view()),
    path("timelines/families/", views.TimelineFamiliesView.as_view(), name="timeline-families"),
    path("timelines/families", views.TimelineFamiliesView.as_view()),
    path("relations/<str:handle1>/<str:handle2>", views.RelationView.as_view(), name="relation"),
    path("relations/<str:handle1>/<str:handle2>/", views.RelationView.as_view()),
    path("relations/<str:handle1>/<str:handle2>/all", views.RelationsView.as_view(), name="relations-all"),
    path("relations/<str:handle1>/<str:handle2>/all/", views.RelationsView.as_view()),
    path("people/<str:handle>/dna/matches", views.PersonDnaMatchesView.as_view(), name="person-dna-matches"),
    path("people/<str:handle>/dna/matches/", views.PersonDnaMatchesView.as_view()),
    path("people/<str:handle>/ydna", views.PersonYDnaView.as_view(), name="person-ydna"),
    path("people/<str:handle>/ydna/", views.PersonYDnaView.as_view()),
    path("parsers/dna-match", views.DnaMatchParserView.as_view(), name="dna-match-parser"),
    path("parsers/dna-match/", views.DnaMatchParserView.as_view()),
]
