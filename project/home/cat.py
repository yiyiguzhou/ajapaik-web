# encoding: utf-8
from copy import deepcopy
import time
import datetime
from pytz import timezone, utc
from django.contrib.auth import authenticate, login
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.core.urlresolvers import reverse
from django.db import IntegrityError
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render_to_response
from django.template import RequestContext
from rest_framework.decorators import api_view, authentication_classes, permission_classes, parser_classes
from rest_framework.parsers import FormParser
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import exception_handler
from sorl.thumbnail import get_thumbnail
from project.home.forms import CatLoginForm, CatAuthForm, CatAlbumStateForm, CatTagForm, CatFavoriteForm
from project.home.models import CatAlbum, CatTagPhoto, CatPhoto, CatTag, CatUserFavorite
from rest_framework import authentication
from rest_framework import exceptions
import random
from project.settings import TIME_ZONE


class CustomAuthentication(authentication.BaseAuthentication):
    @parser_classes((FormParser,))
    def authenticate(self, request):
        cat_auth_form = CatAuthForm(request.data)
        user = None
        if cat_auth_form.is_valid():
            user_id = cat_auth_form.cleaned_data['_u']
            session_id = cat_auth_form.cleaned_data['_s']
            if not session_id or not user_id:
                return None
            try:
                Session.objects.get(session_key=session_id)
                user = User.objects.get(pk=user_id)
            except (User.DoesNotExist, Session.DoesNotExist):
                raise exceptions.AuthenticationFailed('No user/session')

        return user, None


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    # TODO: Error handling
    if response is not None:
        response.data['error'] = 1

    return response


@api_view(['POST'])
@parser_classes((FormParser,))
@permission_classes((AllowAny,))
def cat_login(request):
    login_form = CatLoginForm(request.data)
    content = {
        'error': 0,
        'session': None,
        'expires': 86400
    }
    user = None
    if login_form.is_valid():
        uname = login_form.cleaned_data['username'][:30]
        pw = login_form.cleaned_data['password']
        try:
            user = authenticate(username=uname, password=pw)
        except ObjectDoesNotExist:
            pass
        if not user and login_form.cleaned_data['type'] == 'auto':
            User.objects.create_user(username=uname, password=pw)
            user = authenticate(username=uname, password=pw)
    else:
        content['error'] = 2
    if user:
        login(request, user)
        content['id'] = user.id
        content['session'] = request.session.session_key
    else:
        content['error'] = 4
    return Response(content)


@api_view(['POST'])
@parser_classes((FormParser,))
@authentication_classes((CustomAuthentication,))
@permission_classes((IsAuthenticated,))
def cat_logout(request):
    try:
        session_id = request.data['_s']
    except KeyError:
        return Response({'error': 4})
    try:
        Session.objects.get(pk=session_id).delete()
        return Response({'error': 0})
    except ObjectDoesNotExist:
        return Response({'error': 2})


def cat_album_thumb(request, album_id, thumb_size=250):
    cache_key = "ajapaik_cat_album_thumb_response_%s_%s" % (album_id, thumb_size)
    cached_response = cache.get(cache_key)
    if cached_response:
        return cached_response
    a = get_object_or_404(CatAlbum, id=album_id)
    thumb_str = str(thumb_size) + 'x' + str(thumb_size)
    im = get_thumbnail(a.image, thumb_str, upscale=False)
    content = im.read()
    next_week = datetime.datetime.now() + datetime.timedelta(seconds=604800)
    response = HttpResponse(content, content_type='image/jpg')
    response['Content-Length'] = len(content)
    response['Cache-Control'] = "max-age=604800, public"
    response['Expires'] = next_week.strftime("%a, %d %b %y %T GMT")
    cache.set(cache_key, response)

    return response


@api_view(['POST'])
@authentication_classes((CustomAuthentication,))
@permission_classes((IsAuthenticated,))
def cat_albums(request):
    error = 0
    albums = CatAlbum.objects.all().order_by('-created')
    ret = []
    for a in albums:
        user_tagged_all_in_album = \
            a.photos.count() == CatTagPhoto.objects.filter(profile=request.get_user().profile).distinct('photo').count()
        if user_tagged_all_in_album:
            user_tagged_all_in_album = 1
        else:
            user_tagged_all_in_album = 0
        ret.append({
            'id': a.id,
            'title': a.title,
            'subtitle': a.subtitle,
            'image': request.build_absolute_uri(reverse('project.home.cat.cat_album_thumb', args=(a.id,))),
            'tagged': user_tagged_all_in_album,
        })
    content = {
        'error': error,
        'albums': ret
    }

    return Response(content)


def _get_album_state(request, form):
    content = {
        'error': 0,
        'photos': [],
        'state': str(int(round(time.time() * 1000)))
    }
    if form.is_valid():
        all_cat_tags = set(CatTag.objects.filter(active=True).values_list('name', flat=True))
        album = form.cleaned_data['id']
        content['title'] = album.title
        content['subtitle'] = album.subtitle
        content['image'] = request.build_absolute_uri(reverse('project.home.cat.cat_album_thumb', args=(album.id,)))
        user_tags = CatTagPhoto.objects.filter(profile=request.get_user().profile, album=album)\
            .values('photo').annotate(tag_count=Count('profile'))
        tag_count_dict = {}
        for each in user_tags:
            tag_count_dict[each['photo']] = each['tag_count']
        for p in album.photos.all():
            available_cat_tags = all_cat_tags - set(CatTagPhoto.objects.filter(
                profile=request.get_user().profile, album=album, photo=p).values_list('tag__name', flat=True))
            if form.cleaned_data['max'] == 0:
                to_get = len(available_cat_tags)
            elif 'max' not in form.cleaned_data or form.cleaned_data['max'] is None:
                to_get = 2
            else:
                to_get = form.cleaned_data['max']
            content['photos'].append({
                'id': p.id,
                'image': request.build_absolute_uri(reverse('project.home.cat.cat_photo', args=(p.id,))) + '[DIM]/',
                'title': p.title,
                'author': p.author,
                'user_tags': tag_count_dict[p.id] if p.id in tag_count_dict else 0,
                'source': {'name': p._get_source_with_key(), 'url': p.source_url},
                'tag': random.sample(available_cat_tags, min(len(available_cat_tags), to_get))
            })
        content['photos'] = sorted(content['photos'],
                                   key=lambda y: (y['user_tags'], random.randint(0, len(content['photos']))))
        for each in content['photos']:
            del each['user_tags']
    else:
        content['error'] = 2

    return content


def _utcisoformat(dt):
    return dt.astimezone(utc).replace(tzinfo=None).isoformat()[:-3] + 'Z'


def _get_favorite_object_json_form(request, obj):
    return {
        'id': obj.id,
        'album_id': obj.album.id,
        'photo_id': obj.photo.id,
        'image': request.build_absolute_uri(reverse('project.home.cat.cat_photo', args=(obj.photo.id,))) + '[DIM]/',
        'date': _utcisoformat(obj.created)
    }


def _get_user_data(request, remove_favorite=None, add_favorite=None):
    profile = request.get_user().profile
    user_cat_tags = CatTagPhoto.objects.filter(profile=profile)
    content = {
        'error': 0,
        'tagged': user_cat_tags.count(),
        'pics': user_cat_tags.distinct('photo').count(),
        'message': None,
        'link': None,
    }
    if remove_favorite or add_favorite:
        if remove_favorite:
            content['favorites-'] = [remove_favorite]
        elif add_favorite:
            content['favorites+'] = [_get_favorite_object_json_form(request, add_favorite)]
    else:
        user_favorites = CatUserFavorite.objects.filter(profile=profile)
        content['favorites'] = []
        for f in user_favorites:
            content['favorites'].append(_get_favorite_object_json_form(request, f))

    return content


@api_view(['POST'])
@parser_classes((FormParser,))
@authentication_classes((CustomAuthentication,))
@permission_classes((IsAuthenticated,))
def cat_album_state(request):
    cat_album_state_form = CatAlbumStateForm(request.data)
    return Response(_get_album_state(request, cat_album_state_form))


def cat_photo(request, photo_id, thumb_size=600):
    cache_key = "ajapaik_cat_photo_response_%s_%s" % (photo_id, thumb_size)
    cached_response = cache.get(cache_key)
    if cached_response:
        return cached_response
    p = get_object_or_404(CatPhoto, id=photo_id)
    thumb_str = str(thumb_size) + 'x' + str(thumb_size)
    im = get_thumbnail(p.image, thumb_str, upscale=False)
    content = im.read()
    next_week = datetime.datetime.now() + datetime.timedelta(seconds=604800)
    response = HttpResponse(content, content_type='image/jpg')
    response['Content-Length'] = len(content)
    response['Cache-Control'] = "max-age=604800, public"
    response['Expires'] = next_week.strftime("%a, %d %b %y %T GMT")
    cache.set(cache_key, response)

    return response


@api_view(['POST'])
@parser_classes((FormParser,))
@authentication_classes((CustomAuthentication,))
@permission_classes((IsAuthenticated,))
def cat_tag(request):
    cat_tag_form = CatTagForm(request.data)
    content = {
        'error': 0,
    }
    if cat_tag_form.is_valid():
        tag = CatTagPhoto(
            tag=cat_tag_form.cleaned_data['tag'],
            album=cat_tag_form.cleaned_data['id'],
            photo=cat_tag_form.cleaned_data['photo'],
            profile=request.get_user().profile,
            value=cat_tag_form.cleaned_data['value']
        )
        tag.save()
        content['state'] = str(int(round(time.time() * 1000)))
    else:
        content['error'] = 2

    return Response(content)


@api_view(['POST'])
@parser_classes((FormParser,))
@authentication_classes((CustomAuthentication,))
@permission_classes((IsAuthenticated,))
def user_me(request):
    content = _get_user_data(request)

    return Response(content)


@api_view(['POST'])
@parser_classes((FormParser,))
@authentication_classes((CustomAuthentication,))
@permission_classes((IsAuthenticated,))
def user_favorite_add(request):
    cat_favorite_form = CatFavoriteForm(request.data)
    profile = request.get_user().profile
    content = {
        'error': 2
    }
    if cat_favorite_form.is_valid():
        try:
            cat_user_favorite = CatUserFavorite(
                album=cat_favorite_form.cleaned_data['album'],
                photo=cat_favorite_form.cleaned_data['photo'],
                profile=profile
            )
            cat_user_favorite.save()
            content = _get_user_data(request, add_favorite=cat_user_favorite)
        except IntegrityError:
            content = _get_user_data(request)
            content['error'] = 2

    return Response(content)


@api_view(['POST'])
@parser_classes((FormParser,))
@authentication_classes((CustomAuthentication,))
@permission_classes((IsAuthenticated,))
def user_favorite_remove(request):
    cat_favorite_form = CatFavoriteForm(request.data)
    profile = request.get_user().profile
    content = {
        'error': 2
    }
    if cat_favorite_form.is_valid():
        try:
            cat_user_favorite = CatUserFavorite.objects.get(
                album=cat_favorite_form.cleaned_data['album'],
                photo=cat_favorite_form.cleaned_data['photo'],
                profile=profile
            )
            user_favorite_id = deepcopy(cat_user_favorite.id)
            cat_user_favorite.delete()
            content = _get_user_data(request, remove_favorite=user_favorite_id)
        except ObjectDoesNotExist:
            content = _get_user_data(request)
            content['error'] = 2

    return Response(content)


def cat_results(request, page=1):
    tagged_photos = list(CatPhoto.objects.annotate(tag_count=Count('tags')).filter(tag_count__gt=0).order_by(
        '-tag_count')[(int(page) - 1) * 20: int(page) * 20])
    photo_tags = CatTagPhoto.objects.filter(photo_id__in=[x.id for x in tagged_photos])
    tags = CatTag.objects.all()
    tag_tally = {}
    # FIXME: All this needs to be done with SQL, then again, not so important anyway
    for tp in tagged_photos:
        tag_tally[tp.id] = {}
        for tag in tags:
            tag_tally[tp.id][tag.name] = [0, 0, 0]
    for pt in photo_tags:
        if pt.value == -1:
            tag_tally[pt.photo_id][pt.tag.name][0] += 1
        elif pt.value == 0:
            tag_tally[pt.photo_id][pt.tag.name][1] += 1
        else:
            tag_tally[pt.photo_id][pt.tag.name][2] += 1
    ret = []
    for tp in tagged_photos:
        tp.my_tags = {}
        for t in tags:
            one_or_other = t.name.split('_or_')
            vals = tag_tally[tp.id][t.name]
            greatest_index = vals.index(max(vals))
            if greatest_index == 0 and vals[greatest_index] > 0:
                tp.my_tags[one_or_other[0]] = vals[greatest_index]
            elif greatest_index == 2 and vals[greatest_index] > 0:
                tp.my_tags[one_or_other[1]] = vals[greatest_index]
            elif greatest_index == 1:
                if vals[greatest_index] == 0:
                    del(tp.my_tags[t.name + ' NA'])
                else:
                    tp.my_tags[t.name + ' NA'] = vals[greatest_index]
        ret.append((tp, tp.my_tags))
    return render_to_response('cat_results.html', RequestContext(request, {
        'photos': ret,
        'next': int(page) + 1
    }))