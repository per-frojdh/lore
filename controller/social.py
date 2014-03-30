"""
    controller.social
    ~~~~~~~~~~~~~~~~

    This is the controller and Flask blueprint for social features,
    it initializes URL routes based on the Resource module and specific
    ResourceAccessStrategy for each related model class. This module is then
    responsible for taking incoming URL requests, parse their parameters,
    perform operations on the Model classes and then return responses via 
    associated template files.

    :copyright: (c) 2014 by Raconteur
"""

from flask import abort, request, redirect, url_for, render_template, flash, Blueprint, g, current_app
from resource import ResourceHandler, ResourceError, ResourceAccessStrategy
from model.user import User, Group, Member, Conversation, Message


class SameUserResourceAccessStrategy(ResourceAccessStrategy):
  def is_allowed(self, user, op, instance):
    return user.admin or user == instance or op in ["view", "list"]

social = Blueprint('social', __name__, template_folder='../templates/social')

user_strategy = SameUserResourceAccessStrategy(User, 'users', 'username')
ResourceHandler.register_urls(social, user_strategy)

group_strategy = ResourceAccessStrategy(Group, 'groups', 'slug')
ResourceHandler.register_urls(social, group_strategy)

member_strategy = ResourceAccessStrategy(Member, 'members', None, parent_strategy=group_strategy)

class MemberHandler(ResourceHandler):
  def form_new(self, r):
    r = super(MemberHandler, self).form_new(r)
    # Remove existing member from the choice of new user in Member form
    current_member_ids = [m.user.id for m in r['group'].members]
    r['member_form'].user.queryset = r['member_form'].user.queryset.filter(id__nin=current_member_ids)
    return r

  def form_edit(self, r):
    r = super(MemberHandler, self).form_edit(r)
    current_member_ids = [m.user.id for m in r['group'].members]
    r['member_form'].user.queryset = r['member_form'].user.queryset.filter(id__nin=current_member_ids)
    return r

MemberHandler.register_urls(social, member_strategy)

conversation_strategy = ResourceAccessStrategy(Conversation, 'conversations')

class ConversationHandler(ResourceHandler):
  def new(self, r):
    if not request.form.has_key('content') or len(request.form.get('content'))==0:
      raise ResourceError(400, 'Need to attach first message with conversation')
    r = super(ConversationHandler, self).new(r)
    Message(content=request.form.get('content'), user=g.user, conversation=r['item']).save()
    return r

  def edit(self, r):
    r = super(ConversationHandler, self).edit(r)
    if request.form.has_key('content') and len(request.form.get('content'))>0:
      Message(content=request.form.get('content'), user=g.user, conversation=r['item']).save()
    return r

ConversationHandler.register_urls(social, conversation_strategy)

message_strategy = ResourceAccessStrategy(Message, 'messages', parent_strategy=conversation_strategy)
ResourceHandler.register_urls(social, message_strategy)

###
### Template filters
###
def is_following(from_user, to_user):
  return from_user.is_following(to_user)

social.add_app_template_filter(is_following)

@social.route('/')
@current_app.login_required
def index():
    following_messages = Message.objects(conversation=None, user__in=g.user.following).order_by('-pub_date')
    return render_template('social/base.html', following_message_list=following_messages)
