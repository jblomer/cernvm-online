from django.conf.urls import patterns, include, url
from django.conf import settings
from django.contrib import admin
from cvmo.settings import URL_PREFIX

urlpatterns = patterns(
    "",
    url(r"^%suser" % URL_PREFIX, include("cvmo.user.urls")),
    url(r"^%sdashboard" % URL_PREFIX, include("cvmo.dashboard.urls")),
    url(r"^%scontext" % URL_PREFIX, include("cvmo.context.urls")),
    url(r"^%svm" % URL_PREFIX, include("cvmo.vm.urls")),
    url(r"^%smarket" % URL_PREFIX, include("cvmo.market.urls")),
    url(r"^%scluster" % URL_PREFIX, include("cvmo.cluster.urls"))
)

#
# DEBUG: Serve media files
#

if settings.DEBUG is True:
    urlpatterns += patterns(
        "",
        url(
            r"^%smedia/(?P<path>.*)$" % URL_PREFIX,
            "django.views.static.serve",
            {"document_root": settings.MEDIA_ROOT, "show_indexes": True}
        )
    )

#
# Admin UI
#

admin.autodiscover()
urlpatterns += patterns(
    "", url(r"^%sadmin" % URL_PREFIX, include(admin.site.urls))
)

#
# Core UI
#

urlpatterns += patterns(
    "",
    url(r"^%s" % URL_PREFIX, include("cvmo.core.urls")),
)
