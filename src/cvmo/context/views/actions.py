from django.http import HttpResponse
from django.shortcuts import render_to_response
from django.template import RequestContext

from cvmo.context.models import ContextDefinition, Machines
from cvmo.core.plugins import ContextPlugins
from cvmo.core.utils.views import uncache_response

def edit(request):
    return render_to_response('context/actions_edit.html', {

    }, RequestContext(request))

def save(request):
    pass