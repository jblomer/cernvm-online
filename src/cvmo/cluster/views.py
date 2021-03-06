import re
import json
import base64
import logging
import hashlib
from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect
from django.contrib import messages
from querystring_parser import parser
from .forms import ClusterForm, EC2Form, QuotaForm, ElastiqForm
from .models import ClusterDefinition
from ..context.models import ContextDefinition, ContextStorage
from cvmo.core.utils.views import uncache_response
from cvmo.core.utils.context import salt_context_key
import cvmo.core.utils.crypt as cvmo_crypt


#
# Views
#


def show_new(request, cluster_id=None):

    if cluster_id is None:
        # New context
        return _show_cluster_def(request, {})
    else:

        #
        # Cloning existing context
        #

        # Try to fetch context from the database. Don't filter on current user.
        try:

            cluster = ClusterDefinition.objects.get( id=cluster_id )

            # Unmarshal data
            try:

                if cluster.is_encrypted:
                    post_dict = parser.parse( unicode(request.POST.urlencode()).encode("utf-8") )
                    try:
                        cluster.decrypt( post_dict['password'] )
                    except ClusterDefinition.CryptographyError:
                        messages.error(request, 'Wrong password')
                        return _show_cluster_def(request, {})

                cluster_data_dict = json.loads( cluster.data )

            except ValueError:
                messages.error(request, 'Corrupted cluster data: creating a new cluster.')
                return _show_cluster_def(request, {})

            # Mangle the dictionary to suit form structure
            cluster_data_dict['cluster'] = {
                'master_context_id': cluster.master_context_id,
                'worker_context_id': cluster.worker_context_id,
                'id': cluster.id,
                'name': cluster.name,
                'description': cluster.description
            }
            if 'passphrase' in cluster_data_dict:
                cluster_data_dict['cluster']['passphrase'] = cluster_data_dict['passphrase']
                del cluster_data_dict['passphrase']

            #return uncache_response(HttpResponse(json.dumps(cluster_data_dict, indent=2), content_type="text/plain"))
            return _show_cluster_def(request, cluster_data_dict)

        except ClusterDefinition.DoesNotExist, Http404:
            messages.error(request, 'The specified cluster does not exist: creating a new cluster instead.')
            return _show_cluster_def(request, {})

        #return uncache_response( HttpResponse(my_text_response, content_type='text/plain' ) )


def show_test(request):
    return _show_cluster_def(request, {
        "elastiq": {
            "n_jobs_per_vm": 4,
            "estimated_vm_deploy_time_s": 600,
            "batch_plugin": "condor"
        },
        "quota": {
            "min_vms": 2,
            "max_vms": ""
        },
        "ec2": {
            "api_url": "http://openstack.cern.ch:8773/services/Cloud",
            "api_version": "",
            "aws_access_key_id": "6bebac94cd4b4c0da1af747ae98468e5",
            "aws_secret_access_key": "de5587ddc50f422683e1873e0875061b",
            "image_id": "ami-000001f3",
            "key_name": "glestari",
            "flavour": "m1.small"
        },
        "cluster": {
            "id": "",
            "name": "Hello world",
            "description": "",
            "master_context_id": "32c2291ae49a4d889217488a404a96a1",
            "worker_context_id": "99aa82a1b14f494088d4afa459ca8515"
        }
    })


def show_edit(request, cluster_id):
    return _show_cluster_def(request, {})


def show_deploy(request, cluster_id):


    #get_dict = parser.parse( unicode(request.GET.urlencode()).encode("utf-8") )
    #return uncache_response(HttpResponse(json.dumps(get_dict, indent=2), content_type="text/plain"))
    #return uncache_response(HttpResponse( get_dict['password'] , content_type="text/plain"))

    context = {}
    password = None

    try:
        cluster = ClusterDefinition.objects.get( id=cluster_id )

        if cluster.encryption_checksum != '':
            try:
                get_dict = parser.parse( unicode(request.GET.urlencode()).encode("utf-8") )
                password = get_dict['password']
                cluster.decrypt(password)
            except Exception as e:
                #messages.error(request, "The password provided is wrong: %s" % e)
                messages.error(request, "The password provided is wrong")
                return render(request, "cluster/deploy.html", {})

    except ClusterDefinition.DoesNotExist, Http404:
        messages.error(request, 'Cluster does not exist')
        return render(request, "cluster/deploy.html", {})

    # decrypt goes here #

    try:
        cluster_data_dict = json.loads( cluster.data )
    except ValueError:
        messages.error(request, 'Corrupted cluster data!')
        return render(request, "cluster/deploy.html", {})

    context['ec2'] = {
        'key_name': cluster_data_dict['ec2']['key_name'],
        'flavour': cluster_data_dict['ec2']['flavour'],
        'image_id': cluster_data_dict['ec2']['image_id']
    }

    ami_ctx = _render_head_context( cluster.deployable_context, password )
    context["dc_user_data"] = ami_ctx
    context["dc_user_data_b64"] = base64.b64encode(ami_ctx)

    #return uncache_response(HttpResponse( json.dumps(context, indent=4) , content_type="text/plain"))

    return render(request, "cluster/deploy.html", context)

#
# Actions
#


def save(request):

    # For debug
    # post_dict = parser.parse( unicode(request.POST.urlencode()).encode("utf-8") )
    # return uncache_response(HttpResponse(json.dumps(post_dict, indent=2), content_type="text/plain"))

    l = logging.getLogger("cvmo")

    # Validate request
    resp = _validate_for_save(request)
    if isinstance(resp, HttpResponse):
        return resp

    # For debug: output validated form
    # return uncache_response(HttpResponse(json.dumps(resp, indent=2), content_type="text/plain"))

    #
    # Preparing the full Master Context.
    #
    # This is a full new context available in ContextStorage, but with no corresponding
    # ContextDefinition. This context is the original Master Context plus some sections
    # appended.
    #
    # Note that the full Master Context is generated unencrypted, unless an appropriate
    # Cluster Password is provided, even if the original Master Context was secure.
    #

    try:
        (plg_name, plg_cont) = _render_elastiq_plugin(resp)
    except Exception:
        messages.error(request, 'Wrong Worker Context password supplied!')
        return _show_cluster_def(request, resp)

    # For debug: output generated extra section (with worker user-data embedded)
    # return uncache_response(HttpResponse(json.dumps({'plg_name':plg_name, 'plg_cont':plg_cont}, indent=2), content_type="text/plain"))

    master_ctx = ContextStorage.objects.get( id=resp["cluster"]["master_context_id"] )

    if master_ctx.is_encrypted:

        try:

            mpwd = resp['cluster']['master_context_pwd']
            mdef = ContextDefinition.objects.get( id=resp['cluster']['master_context_id'] )

            # Verify password before decrypting
            if salt_context_key(mdef.id, mpwd) == mdef.key:
                master_ctx.decrypt(mpwd)
            else:
                raise Exception()

        except Exception:

            messages.error(request, 'Wrong Head Context password supplied!')
            return _show_cluster_def(request, resp)

    new_ud = _append_plugin_in_ud(master_ctx.ec2_user_data, plg_name, plg_cont)
    if not new_ud:
        messages.error(
            request,
            "Failed to append `elastiq-setup` plugin in master context!"
        )
        l.log(
            logging.ERROR,
            "Failed to append `elastiq-setup` plugin in master context %s!"
            % resp["cluster"]["master_context_id"]
        )
        return _show_cluster_def(request, resp)

    # Do we have a passphrase for the cluster?
    if 'passphrase' in resp['cluster']:
        passphrase = resp['cluster']['passphrase']
    else:
        # Never None
        passphrase = ''

    #
    # Store the deployable context (rendered)
    #

    context_id = ContextDefinition.generate_new_id()
    cs = ContextStorage.create(
        context_id, "Cluster %s head node" % resp["cluster"]["name"],
        new_ud, master_ctx.root_ssh_key
    )
    if passphrase != '':
        cs.encrypt( passphrase )
    cs.save()

    #
    # Create the Cluster Definition
    #

    # First create a string representing the input data
    data = {}
    for k in [ 'ec2', 'quota', 'elastiq' ]:
        if k in resp:
            data[k] = resp[k]
        else:
            data[k] = {}

    # Let's store some cluster variables here as well

    data['passphrase'] = passphrase

    data_json_str = json.dumps(data, indent=2)
    #return uncache_response( HttpResponse( data_json_str, content_type='text/plain' ) )

    # Encrypt the Cluster Definition with the given passphrase
    if passphrase != '':

        # To verify, we calculate the SHA1SUM of the unencrypted JSON blob and store it
        checksum = hashlib.sha1( data_json_str ).hexdigest()

        # Now, we encrypt
        data_json_str = base64.b64encode( cvmo_crypt.encrypt(data_json_str, passphrase) )

    else:
        # Non-encrypted, plain text context
        checksum = ''

    cd = ClusterDefinition(
        name=resp["cluster"]["name"],
        description=resp["cluster"].get("description", None),
        owner=request.user,
        master_context=ContextDefinition.objects.get(
            id=resp["cluster"]["master_context_id"]
        ),
        worker_context=ContextDefinition.objects.get(
            id=resp["cluster"]["worker_context_id"]
        ),
        deployable_context=cs,
        data=data_json_str,
        encryption_checksum=checksum,
    )
    cd.save()

    messages.success(
        request, "Cluster '%s' was successfully stored!" % cd.name
    )
    return redirect("dashboard")


def delete(request, cluster_id):
    try:
        cluster = ClusterDefinition.objects.get(
            id=cluster_id,
            owner=request.user
        )
    except ClusterDefinition.DoesNotExist:
        raise Http404(
            "Cluster with id `%s` does not exist or it is not yours."
            % cluster_id
        )

    # Delete context
    cluster.deployable_context.delete()
    name = cluster.name
    cluster.delete()

    messages.success(
        request, "Cluster '%s' was successfully deleted!" % name
    )
    return redirect("dashboard")

#
# Helpers
#


def _render_head_context(cs_instance, password=None):

    contextualization_key = cs_instance.id

    if cs_instance.is_encrypted and password is not None:
        cs_instance.decrypt(password)
        contextualization_key += ':' + password

    ud = cs_instance.ec2_user_data

    # Get the part between [ucernvm-begin] and [ucernvm-end]
    r = re.compile(r"^\s*\[ucernvm-begin\]\s*$", re.M)
    g = r.search(ud)
    s = ud[g.end():].strip()
    r = re.compile(r"^\s*\[ucernvm-end\]\s*$", re.M)
    g = r.search(s)
    ucvm_ctx = s[:g.start()].strip()

    # Prepare context
    ctx = """[amiconfig]
plugins=cernvm
[cernvm]
contextualization_key=%s
[ucernvm-begin]
%s
[ucernvm-end]
""" % (contextualization_key, ucvm_ctx)

    return ctx


def _append_plugin_in_ud(init_ud, plugin_name, plugin_cont):
    """
    Appends plugin with name `plugin_name` and contents `plugin_cont` in
    user data provided in `init_ud`. Will modify appropriately the `plugins`
    variable in `amiconfig` section.
    """
    new_ud = "%s\n[%s]\n%s\n" % (init_ud, plugin_name, plugin_cont)

    g = re.search(r"\s*(plugins\s*=\s*)(.*)\s*$", init_ud, re.M)
    if not g:
        return False

    plugins = g.group(2).split()
    if plugin_name in plugins:
        return new_ud

    # adding plugin name
    plugins.append(plugin_name)
    new_ud = new_ud.replace(
        g.group(1) + g.group(2),
        g.group(1) + " ".join(plugins))

    return new_ud


def _render_elastiq_plugin(resp):
    """
    Given the clean data from the cluster form it returns a string, the
    elastiq-setup amiconfig plugin settings.
    """
    plg = ""

    # Elastiq: Jobs per VM
    v = resp["elastiq"].get("n_jobs_per_vm", None)
    if v:
        plg += "elastiq_n_jobs_per_vm=%d\n" % v

    # Elastiq: VM deployment time
    v = resp["elastiq"].get("estimated_vm_deploy_time_s", None)
    if v:
        plg += "elastiq_estimated_vm_deploy_time_s=%d\n" % v

    # Elastiq: Queue / VM checking times
    v = resp["elastiq"].get("check_queue_every_s", None)
    if v:
        plg += "elastiq_check_queue_every_s=%d\n" % v
    v = resp["elastiq"].get("check_vms_every_s", None)
    if v:
        plg += "elastiq_check_vms_every_s=%d\n" % v

    # Elastiq: Idle time before killing
    v = resp["elastiq"].get("idle_for_time_s", None)
    if v:
        plg += "elastiq_idle_for_time_s=%d\n" % v

    # Elastiq: Minimum time a job is waiting
    v = resp["elastiq"].get("waiting_jobs_time_s", None)
    if v:
        plg += "elastiq_waiting_jobs_time_s=%d\n" % v

    # Elastiq: Batch sysrem
    v = resp["elastiq"].get("batch_plugin", None)
    if v:
        plg += "elastiq_batch_plugin=%s\n" % v

    # Quota
    plg += "quota_min_vms=%d\n" \
        % resp["quota"].get("min_vms", 2)
    v = resp["quota"].get("max_vms", None)
    if v:
        plg += "quota_max_vms=%d\n" % v

    # EC2: API
    plg += "ec2_api_url=%s\n" \
        % resp["ec2"]["api_url"]
    v = resp["ec2"].get("api_version", None)
    if v:
        plg += "ec2_api_version=%s\n" % v

    # EC2: Access key
    plg += "ec2_aws_access_key_id=%s\n" \
        % resp["ec2"]["aws_access_key_id"]
    plg += "ec2_aws_secret_access_key=%s\n" \
        % resp["ec2"]["aws_secret_access_key"]

    # EC2: Image
    plg += "ec2_image_id=%s\n" \
        % resp["ec2"]["image_id"]
    # EC2: Flavor
    plg += "ec2_flavour=%s\n" % resp["ec2"]["flavour"]

    # EC2: Keypair
    v = resp["ec2"].get("key_name", None)
    if v:
        plg += "ec2_key_name=%s\n" % v

    # user-data of the Worker Context
    wc = ContextStorage.objects.get(id=resp["cluster"]["worker_context_id"])
    if wc.is_encrypted:

        # You *must* handle exceptions in the caller
        wpwd = resp['cluster']['worker_context_pwd']
        wcdef = ContextDefinition.objects.get(id=resp["cluster"]["worker_context_id"])

        # Verify password before decrypting
        if salt_context_key(wcdef.id, wpwd) == wcdef.key:
            wc.decrypt(wpwd)
        else:
            raise cvmo_crypt.DecryptionError('Wrong password supplied')

    plg += "ec2_user_data_b64=%s\n" % base64.b64encode(wc.ec2_user_data)

    return ("elastiq-setup", plg)


def _validate_for_save(request):
    """
    Validates the HttpRequest. It uses the forms defined in forms.py to
    check all the fields and runs some additional checks. It returns
    an HttpResponse instance if request is invalid and the dictionary of clean
    date otherwise.
    """
    data = parser.parse(request.POST.urlencode())
    clean_data = {}

    # Cluster section
    cluster_f = ClusterForm(data.get("cluster", {}))
    if not cluster_f.is_valid():
        for label, msg in cluster_f.errors_list:
            messages.error(request, "Cluster %s: %s" % (label, msg))
        return _show_cluster_def(request, data)
    clean_data["cluster"] = cluster_f.clean()

    # Check that contexts belong to user or is public
    for context_field_code in ["master_context_id", "worker_context_id"]:
        c = ContextDefinition.objects.get(
            id=clean_data["cluster"][context_field_code]
        )
        if not c.public and c.owner != request.user:
            messages.error(
                request,
                "Context with id '%s' is not public and does not belong to\
 you" % (c.id)
            )
            return _show_cluster_def(request, data)
        #if c.is_encrypted:
        #    messages.error(request, "Context '%s' is encrypted!" % (c.name))
        #    return _show_cluster_def(request, data)

    # elastiq section
    elastiq_f = ElastiqForm(data.get("elastiq", {}))
    if not elastiq_f.is_valid():
        for label, msg in elastiq_f.errors_list:
            messages.error(request, "elastiq %s: %s" % (label, msg))
        return _show_cluster_def(request, data)
    clean_data["elastiq"] = elastiq_f.clean()

    # EC2 section
    ec2_f = EC2Form(data.get("ec2", {}))
    if not ec2_f.is_valid():
        for label, msg in ec2_f.errors_list:
            messages.error(request, "EC2 %s: %s" % (label, msg))
        return _show_cluster_def(request, data)
    clean_data["ec2"] = ec2_f.clean()

    # Quota section
    quota_f = QuotaForm(data.get("quota", {}))
    if not quota_f.is_valid():
        for label, msg in quota_f.errors_list:
            messages.error(request, "Quota %s: %s" % (label, msg))
        return _show_cluster_def(request, data)
    clean_data["quota"] = quota_f.clean()

    return clean_data


def _show_cluster_def(request, data={}):
    """
    Given the HttpRequest and an optional dictionary, it renders the cluster
    definition form page. Useful for the empty form of the new cluster,
    for cluster editing and for re-rendering failed forms when client-side
    validation does not work.
    """
    if "cluster" in data:
        for context_code in ["master_context", "worker_context"]:
            cid = data["cluster"].get("%s_id" % context_code, None)
            if cid:
                try:
                    c = ContextDefinition.objects.get(id=cid)
                    if c.owner == request.user or c.public:
                        data["cluster"]["%s_json" % context_code] = json.dumps(
                            {
                                "id": c.id,
                                "name": c.name,
                                "description": c.description,
                                "owner": c.owner.username
                            }
                        )
                    else:
                        del data["cluster"]["%s_id" % context_code]
                        data["cluster"]["%s_json" % context_code] = "{}"
                except ContextDefinition.DoesNotExist:
                    del data["cluster"]["%s_id" % context_code]
                    data["cluster"]["%s_json" % context_code] = "{}"

    return render(
        request,
        "cluster/new.html",
        data
    )
