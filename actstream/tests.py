from random import choice

from django.db import connection
from django.test import TestCase
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.contrib.sites.models import Site
from django.template.loader import Template, Context
from django.utils.translation import ugettext_lazy as _
from django.utils.translation import activate, get_language
from django.utils.six import text_type

from actstream.models import (Action, Follow, model_stream, user_stream,
                              actor_stream, following, followers)
from actstream.actions import follow, unfollow
from actstream.signals import action
from actstream.registry import register, unregister
from actstream.compat import get_user_model


def render(src, **ctx):
    return Template('{% load activity_tags %}' + src).render(Context(ctx))


class LTE(int):
    def __new__(cls, n):
        obj = super(LTE, cls).__new__(cls, n)
        obj.n = n
        return obj

    def __eq__(self, other):
        return other <= self.n

    def __repr__(self):
        return "<= %s" % self.n


class ActivityBaseTestCase(TestCase):
    actstream_models = ()

    def setUp(self):
        for model in self.actstream_models:
            register(model)

    def assertSetEqual(self, l1, l2, msg=None):
        self.assertSequenceEqual(set(map(text_type, l1)), set(l2))

    def assertAllIn(self, bits, string):
        for bit in bits:
            self.assertIn(bit, string)

    def assertAllIn(self, bits, string):
        for bit in bits:
            self.assertIn(bit, string)

    def tearDown(self):
        for model in self.actstream_models:
            unregister(model)


class ActivityTestCase(ActivityBaseTestCase):
    urls = 'actstream.urls'
    actstream_models = ('auth.User', 'auth.Group', 'sites.Site')

    def setUp(self):
        User = get_user_model()
        super(ActivityTestCase, self).setUp()
        self.group = Group.objects.create(name='CoolGroup')
        self.user1 = User.objects.get_or_create(username='admin')[0]
        self.user1.set_password('admin')
        self.user1.is_superuser = self.user1.is_staff = True
        self.user1.save()
        self.user2 = User.objects.get_or_create(username='Two')[0]
        self.user3 = User.objects.get_or_create(username='Three')[0]

        # User1 joins group
        self.user1.groups.add(self.group)
        self.join_action = action.send(self.user1, verb='joined', target=self.group)[0][1]

        # User1 follows User2
        follow(self.user1, self.user2)

        # User2 joins group
        self.user2.groups.add(self.group)
        action.send(self.user2, verb='joined', target=self.group)

        # User2 follows group
        follow(self.user2, self.group)

        # User1 comments on group
        # Use a site object here and predict the "__unicode__ method output"
        action.send(self.user1, verb='commented on', target=self.group)
        self.comment = Site.objects.create(
            domain="admin: Sweet Group!...")

        # Group responds to comment
        action.send(self.group, verb='responded to', target=self.comment)

        # User 3 did something but doesn't following someone
        action.send(self.user3, verb='liked actstream')

    def test_aauser1(self):
        self.assertSetEqual(self.user1.actor_actions.all(), [
            'admin commented on CoolGroup 0 minutes ago',
            'admin started following Two 0 minutes ago',
            'admin joined CoolGroup 0 minutes ago',
        ])

    def test_user2(self):
        self.assertSetEqual(actor_stream(self.user2), [
            'Two started following CoolGroup 0 minutes ago',
            'Two joined CoolGroup 0 minutes ago',
        ])

    def test_group(self):
        self.assertSetEqual(actor_stream(self.group),
            ['CoolGroup responded to admin: Sweet Group!... 0 minutes ago'])

    def test_following(self):
        User = get_user_model()
        self.assertEqual(list(following(self.user1)), [self.user2])
        self.assertEqual(len(following(self.user2, User)), 0)

    def test_followers(self):
        self.assertEqual(list(followers(self.group)), [self.user2])

    def test_empty_follow_stream(self):
        unfollow(self.user1, self.user2)
        self.assertFalse(user_stream(self.user1))

        self.assertSetEqual(
            user_stream(self.user3, with_user_activity=True),
            ['Three liked actstream 0 minutes ago']
        )

    def test_stream(self):
        self.assertSetEqual(user_stream(self.user1), [
            'Two started following CoolGroup 0 minutes ago',
            'Two joined CoolGroup 0 minutes ago',
        ])
        self.assertSetEqual(user_stream(self.user2),
            ['CoolGroup responded to admin: Sweet Group!... 0 minutes ago'])

    def test_stream_stale_follows(self):
        """
        user_stream() should ignore Follow objects with stale actor
        references.
        """
        self.user2.delete()
        self.assertNotIn('Two', str(user_stream(self.user1)))

    def test_rss(self):
        self.assertAllIn([
            '<?xml version="1.0" encoding="utf-8"?>\n',
            '<rss ',
            'xmlns:atom="http://www.w3.org/2005/Atom"',
            'version="2.0"',
            'Activity feed for your followed actors'
        ], self.client.get('/feed/').content.decode())

    def test_atom(self):
        self.assertAllIn([
            '<?xml version="1.0" encoding="utf-8"?>\n',
            'xmlns="http://www.w3.org/2005/Atom"',
            'xml:lang="%s"' % settings.LANGUAGE_CODE,
            'Activity feed for your followed actors'
        ], self.client.get('/feed/atom/').content.decode())

    def test_action_object(self):
        created_action = action.send(self.user1, verb='created comment',
            action_object=self.comment, target=self.group)[0][1]

        self.assertEqual(created_action.actor, self.user1)
        self.assertEqual(created_action.action_object, self.comment)
        self.assertEqual(created_action.target, self.group)
        self.assertEqual(text_type(created_action),
            'admin created comment admin: Sweet Group!... on CoolGroup 0 '
                'minutes ago')

    def test_doesnt_generate_duplicate_follow_records(self):
        User = get_user_model()
        g = Group.objects.get_or_create(name='DupGroup')[0]
        s = User.objects.get_or_create(username='dupuser')[0]

        f1 = follow(s, g)
        self.assertTrue(f1 is not None, "Should have received a new follow "
            "record")
        self.assertTrue(isinstance(f1, Follow), "Returns a Follow object")

        self.assertEqual(1, Follow.objects.filter(user=s, object_id=g.pk,
            content_type=ContentType.objects.get_for_model(g)).count(),
            "Should only have 1 follow record here")

        f2 = follow(s, g)
        self.assertEqual(1, Follow.objects.filter(user=s, object_id=g.pk,
            content_type=ContentType.objects.get_for_model(g)).count(),
            "Should still only have 1 follow record here")
        self.assertTrue(f2 is not None, "Should have received a Follow object")
        self.assertTrue(isinstance(f2, Follow), "Returns a Follow object")
        self.assertEqual(f1, f2, "Should have received the same Follow "
            "object that I first submitted")

    def test_y_no_orphaned_follows(self):
        follows = Follow.objects.count()
        self.user2.delete()
        self.assertEqual(follows - 1, Follow.objects.count())

    def test_z_no_orphaned_actions(self):
        actions = self.user1.actor_actions.count()
        self.user2.delete()
        self.assertEqual(actions - 1, self.user1.actor_actions.count())

    def test_generic_relation_accessors(self):
        self.assertEqual(self.user2.actor_actions.count(), 2)
        self.assertEqual(self.user2.target_actions.count(), 1)
        self.assertEqual(self.user2.action_object_actions.count(), 0)

    def test_hidden_action(self):
        action = self.user1.actor_actions.all()[0]
        action.public = False
        action.save()
        self.assertNotIn(action, self.user1.actor_actions.public())

    def test_tag_follow_url(self):
        src = '{% follow_url user %}'
        output = render(src, user=self.user1)
        ct = ContentType.objects.get_for_model(self.user1)
        self.assertEqual(output, '/follow/%s/%s/' % (ct.pk, self.user1.pk))

    def test_tag_follow_all_url(self):
        src = '{% follow_all_url user %}'
        output = render(src, user=self.user1)
        ct = ContentType.objects.get_for_model(self.user1)
        self.assertEqual(output, '/follow_all/%s/%s/' % (ct.pk, self.user1.pk))

    def test_tag_actor_url(self):
        src = '{% actor_url user %}'
        output = render(src, user=self.user1)
        ct = ContentType.objects.get_for_model(self.user1)
        self.assertEqual(output, '/actors/%s/%s/' % (ct.pk, self.user1.pk))

    def test_tag_display_action(self):
        src = '{% display_action action %}'
        output = render(src, action=self.join_action)
        self.assertAllIn([str(self.user1), 'joined', str(self.group)], output)
        src = '{% display_action action as nope %}'
        self.assertEqual(render(src, action=self.join_action), '')

    def test_tag_activity_stream(self):
        output = render('''{% activity_stream 'actor' user as='mystream' %}
        {% for action in mystream %}
            {{ action }}
        {% endfor %}
        ''', user=self.user1)
        self.assertAllIn([str(action) for action in actor_stream(self.user1)],
                         output)

    def test_model_actions_with_kwargs(self):
        """
        Testing the model_actions method of the ActionManager
        by passing kwargs
        """
        self.assertSetEqual(model_stream(self.user1, verb='commented on'), [
                'admin commented on CoolGroup 0 minutes ago',
                ])

    def test_user_stream_with_kwargs(self):
        """
        Testing the user method of the ActionManager by passing additional
        filters in kwargs
        """
        self.assertSetEqual(user_stream(self.user1, verb='joined'), [
                'Two joined CoolGroup 0 minutes ago',
                ])

    def test_is_following_filter(self):
        src = '{% if user|is_following:group %}yup{% endif %}'
        self.assertEqual(render(src, user=self.user2, group=self.group), 'yup')
        self.assertEqual(render(src, user=self.user1, group=self.group), '')

    def test_store_untranslated_string(self):
        lang = get_language()
        activate("fr")
        verb = _('English')

        assert text_type(verb) == "Anglais"
        action.send(self.user1, verb=verb, action_object=self.comment,
                    target=self.group)
        self.assertTrue(Action.objects.filter(verb='English').exists())
        # restore language
        activate(lang)

    def test_none_returns_an_empty_queryset(self):
        qs = Action.objects.none()
        self.assertFalse(qs.exists())
        self.assertEqual(qs.count(), 0)

    def test_with_user_activity(self):
        self.assertIn(self.join_action,
                      list(user_stream(self.user1, with_user_activity=True)))


class ZombieTest(ActivityBaseTestCase):
    actstream_models = ('auth.User',)
    human = 10
    zombie = 1

    def setUp(self):
        User = get_user_model()
        super(ZombieTest, self).setUp()
        settings.DEBUG = True

        player_generator = lambda n, count: [User.objects.create(
            username='%s%d' % (n, i)) for i in range(count)]

        self.humans = player_generator('human', self.human)
        self.zombies = player_generator('zombie', self.zombie)

        self.zombie_apocalypse()

    def tearDown(self):
        settings.DEBUG = False
        super(ZombieTest, self).tearDown()

    def zombie_apocalypse(self):
        humans = self.humans[:]
        zombies = self.zombies[:]
        while humans:
            for z in self.zombies:
                victim = choice(humans)
                humans.remove(victim)
                zombies.append(victim)
                action.send(z, verb='killed', target=victim)
                if not humans:
                    break

    def check_query_count(self, queryset):
        ci = len(connection.queries)

        result = list([map(text_type, (x.actor, x.target, x.action_object))
            for x in queryset])
        self.assertTrue(len(connection.queries) - ci <= 4,
            'Too many queries, got %d expected no more than 4' %
                len(connection.queries))
        return result

    def test_query_count(self):
        User = get_user_model()
        queryset = model_stream(User)
        result = self.check_query_count(queryset)
        self.assertEqual(len(result), 10)

    def test_query_count_sliced(self):
        User = get_user_model()
        queryset = model_stream(User)[:5]
        result = self.check_query_count(queryset)
        self.assertEqual(len(result), 5)


class GFKManagerTestCase(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user_ct = ContentType.objects.get_for_model(User)
        self.group_ct = ContentType.objects.get_for_model(Group)
        self.group, _ = Group.objects.get_or_create(name='CoolGroup')
        self.user1, _ = User.objects.get_or_create(username='admin')
        self.user2, _ = User.objects.get_or_create(username='Two')
        self.user3, _ = User.objects.get_or_create(username='Three')
        self.user4, _ = User.objects.get_or_create(username='Four')
        Action.objects.get_or_create(
            actor_content_type=self.user_ct,
            actor_object_id=self.user1.id,
            verb='followed',
            target_content_type=self.user_ct,
            target_object_id=self.user2.id
        )
        Action.objects.get_or_create(
            actor_content_type=self.user_ct,
            actor_object_id=self.user1.id,
            verb='followed',
            target_content_type=self.user_ct,
            target_object_id=self.user3.id
        )
        Action.objects.get_or_create(
            actor_content_type=self.user_ct,
            actor_object_id=self.user1.id,
            verb='followed',
            target_content_type=self.user_ct,
            target_object_id=self.user4.id
        )
        Action.objects.get_or_create(
            actor_content_type=self.user_ct,
            actor_object_id=self.user1.id,
            verb='joined',
            target_content_type=self.group_ct,
            target_object_id=self.group.id
        )

    def test_fetch_generic_relations(self):
        # baseline without fetch_generic_relations
        _actions = Action.objects.filter(actor_content_type=self.user_ct,
            actor_object_id=self.user1.id)
        actions = lambda: _actions._clone()
        num_content_types = len(set(actions().values_list(
            'target_content_type_id', flat=True)))
        n = actions().count()

        # compare to fetching only 1 generic relation
        self.assertNumQueries(LTE(n + 1),
            lambda: [a.target for a in actions()])
        self.assertNumQueries(LTE(num_content_types + 2),
            lambda: [a.target for a in
                actions().fetch_generic_relations('target')])

        action_targets = [(a.id, a.target) for a in actions()]
        action_targets_fetch_generic = [(a.id, a.target) for a in
                actions().fetch_generic_relations('target')]
        self.assertEqual(action_targets, action_targets_fetch_generic)

        # compare to fetching all generic relations
        num_content_types = len(set(sum(actions().values_list(
            'actor_content_type_id', 'target_content_type_id'), ())))
        self.assertNumQueries(LTE(2 * n + 1),
            lambda: [(a.actor, a.target) for a in actions()])
        self.assertNumQueries(LTE(num_content_types + 2),
            lambda: [(a.actor, a.target) for a in
                actions().fetch_generic_relations()])

        action_actor_targets = [(a.id, a.actor, a.target) for a in actions()]
        action_actor_targets_fetch_generic_all = [
            (a.id, a.actor, a.target) for a in
                actions().fetch_generic_relations()]
        self.assertEqual(action_actor_targets,
            action_actor_targets_fetch_generic_all)

        # fetch only 1 generic relation, but access both gfks
        self.assertNumQueries(LTE(n + num_content_types + 2),
            lambda: [(a.actor, a.target) for a in
                actions().fetch_generic_relations('target')])
        action_actor_targets_fetch_generic_target = [
            (a.id, a.actor, a.target) for a in
                actions().fetch_generic_relations('target')]
        self.assertEqual(action_actor_targets,
            action_actor_targets_fetch_generic_target)
