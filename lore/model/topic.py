from ctypes import ArgumentError
import json
import logging
import re
from datetime import datetime, timedelta
from typing import List, Union, Any
from os.path import join

from bson.dbref import DBRef
from flask import current_app, g, url_for
from flask_babel import lazy_gettext as _
from mongoengine import (
    DENY,
    NULLIFY,
    BooleanField,
    DateTimeField,
    Document,
    DynamicField,
    EmailField,
    EmbeddedDocument,
    EmbeddedDocumentListField,
    FloatField,
    IntField,
    LazyReferenceField,
    ListField,
    StringField,
    URLField,
)
from mongoengine.base import LazyReference
from mongoengine.errors import DoesNotExist
from mongoengine.queryset.queryset import QuerySet

from lore.model.misc import (
    RegexQueriableStringField,
    datetime_delta_options,
    distinct_options,
    extract,
    numerical_options,
)
from tools.unicode_slugify import SLUG_OK, slugify

logger = current_app.logger if current_app else logging.getLogger(__name__)


def get_id(topic):
    try:
        return topic.id
    except Exception:
        return str(topic)


def clean_scopes(scopes):
    if scopes is None:
        scopes = []
    else:
        # Remove falsy scopes, remove duplicates and sort
        scopes = sorted(set([s for s in scopes if s]))  # Remove empty strings and similar in scopes
    return scopes


def scopes_to_str(scopes):
    return ",".join(map(get_id, scopes))


LORE_BASE = "lore.pub/t/"


class Name(EmbeddedDocument):
    name = RegexQueriableStringField()
    scopes = ListField(LazyReferenceField("Topic"))  # Expected to always be sorted

    def __repr__(self):
        return self.name if not self.scopes else f"{self.name} ({scopes_to_str(self.scopes)})"


class Occurrence(EmbeddedDocument):
    uri = StringField()
    content = StringField()  # Could be any inline data?
    kind = LazyReferenceField("Topic")
    scopes = ListField(LazyReferenceField("Topic"))  # Expected to always be sorted

    def creator(self):
        # TODO stupid way of getting a user. What if more users?
        users = [s for s in self.scopes if "@" in s.pk]
        return users[0] if users else None

    def contribution_scope(self):
        contributions = [s for s in self.scopes if s.pk in {f"{LORE_BASE}canon", f"{LORE_BASE}community", f"{LORE_BASE}contrib"}]
        return contributions[0] if contributions else None

    def __repr__(self):
        out = self.kind.pk if self.kind else "occurrence"
        if self.uri:
            out += ": uri"
        elif self.content:
            out += ": content"
        if self.scopes:
            out += f" ({scopes_to_str(self.scopes)})"
        return out


class Association(EmbeddedDocument):
    r1 = LazyReferenceField("Topic")  # E.g. Employs
    kind = LazyReferenceField("Topic")  # E.g. Employment
    r2 = LazyReferenceField("Topic")  # E.g. employed by
    t2 = LazyReferenceField("Topic")
    scopes = ListField(LazyReferenceField("Topic"))  # Expected to always be sorted

    def contribution_scope(self):
        contributions = [s for s in self.scopes if s.pk in {f"{LORE_BASE}canon", f"{LORE_BASE}community", f"{LORE_BASE}contrib"}]
        return contributions[0] if contributions else None

    def __repr__(self):
        out = f"this ({self.r1.pk}) -> {self.t2.pk} ({self.r2.pk})"
        if self.scopes:
            out += f" ({scopes_to_str(self.scopes)})"
        return out

    # Alternative ways of storing
    # [
    #   instance1: [{r1, r2, t2, scopes}],
    #   instance2: [{r1, r2, t2, scopes}],
    # ]
    # No roles? (store role with instance topic)
    # [
    #   instance1: {
    #        [{t2, scopes}, {t2, scopes}]
    #   }
    # ]


unset = "UNSET"


# Queries
# Find from publisher: find({_id:/^lore.pub/})
# Topics with name in English: find({"names.scopes": "lore.pub/t/en"})  - picks documents where en is in scopes
# Topics associated to other topic: find({"associations.topic": "helmgast.se/eon/asharisk", "associations.r2": "lore.pub/t/target"})
# Topics contributed by user: find({"associations.scopes": "ola@drangopedia.lore.pub"}) - can add or to check also occurrences and names
# Searching a topic by alternative names find({"names.name": "Alternative name"})  <- needs an index in this case

# Local finds
# English name: find_names(scopes={en})
# Only canon content: needs changes to find_x methods, to not match given scopes.


class Topic(Document):
    meta = {
        "indexes": ["kind", "names.name", {"fields": ["$names.name", "$occurrences.content"]}],
        # 'auto_create_index': True
    }

    id = StringField(primary_key=True)
    kind = LazyReferenceField("Topic", verbose_name=_("Type"))
    created_at = DateTimeField(default=datetime.utcnow, verbose_name=_("Created"))
    updated_at = DateTimeField(default=datetime.utcnow)

    names = EmbeddedDocumentListField(Name, verbose_name=_("Name"))
    occurrences = EmbeddedDocumentListField(Occurrence)
    associations = EmbeddedDocumentListField(Association)

    def clean(self):
        self.updated_at = datetime.utcnow()

    @property  # For convenience
    def name(self):
        try:
            return self.names[0].name
        except IndexError:
            return self.id.rsplit("/")[-1]

    @property  # For convenience
    def article(self):
        article = self.find_occurrences(kind=f"{LORE_BASE}article", first=True)
        return article.content if article else ""

    @property  # For convenience
    def bibrefs(self):
        return self.find_occurrences(kind=f"{LORE_BASE}bibref")

    @property  # For convenience
    def description(self):
        desc = self.find_occurrences(kind=f"{LORE_BASE}description")
        if not desc:
            desc = [
                o
                for o in self.occurrences
                if o.kind.pk in {f"{LORE_BASE}article", f"{LORE_BASE}website", f"{LORE_BASE}google_doc"} and o.content
            ]
        return desc[0] if len(desc) > 0 else ""

    def occurrences_except_content(self, except_content):
        return [o for o in self.occurrences if o.content not in except_content]

    def alt_names(self, exclude_name):
        return [name_obj for name_obj in self.names if name_obj.name != exclude_name]

    def as_article_url(self, **kwargs):
        """Temporary function needed to make a topic id into the parts needed to go to an Article.
        """
        parts: List[str] = self.id.split("/")
        pub, world, article = "", "", ""
        if len(parts) > 0:
            pub = parts[0]
        if len(parts) > 1:
            world = parts[1]
        if len(parts) > 2:
            article = "/".join(parts[2:])
        if article:
            return url_for("world.ArticlesView:get", id=article, world_=world, pub_host=pub)
        elif world:
            return url_for("world.ArticlesView:world_home", world_=world, pub_host=pub)
        elif pub:
            return url_for("world.ArticlesView:publisher_home", pub_host=pub)
        else:
            return self.id

    def safe_ref_name(self, ref):
        """Safely tries to get the name of a topic field, even if it's null or can't be fetched.

        Args:
            ref (): [description]
        """
        if ref is not None:
            if isinstance(ref, LazyReference):
                try:
                    return ref.fetch().name
                except Exception:
                    return ref.pk
            elif isinstance(ref, str):
                return ref
        return ""

    # def merge
    # to merge two topics, we need to:
    # merge list of names and associations, without adding duplicates (but maybe merging scopes together)
    # go through all associations, change the That-side to point to new topic instead of old. Also go through the content of That-side to convert links.

    # TODO consider using https://docs.mongoengine.org/apireference.html#embedded-document-querying to query
    # lists of names, occ, ass

    def find_names(self, name: str = unset, scopes: Any = unset, first: bool = False, indices: bool = False):
        """Finds names on this topic that matches the complete or partial arguments.
        Returns a list.
        """
        if scopes != unset:
            scopes = set(map(get_id, scopes))

        # Scopes match if provided scopes is a subset of the name's scopes
        found = []
        for i, x in enumerate(self.names):
            if (name == unset or name == x.name) and (scopes == unset or scopes <= set(map(get_id, x.scopes))):
                found.append((i, x)) if indices else found.append(x)
                if first:  # Return early for speed
                    return found[0]

        return found if not first else None

    def find_occurrences(
        self,
        uri: str = unset,
        content: str = unset,
        kind: str = unset,
        scopes: Any = unset,
        first: bool = False,
        indices: bool = False,
    ):
        """Finds occurrences on this topic that matches the complete or partial arguments.
        Returns a list.
        """
        if scopes != unset:
            scopes = set(map(get_id, scopes))

        found = []
        for i, x in enumerate(self.occurrences):
            if (
                (uri == unset or uri == x.uri)
                and (content == unset or content == x.content)
                and (kind == unset or get_id(kind) == get_id(x.kind))
                and (scopes == unset or scopes <= set(map(get_id, x.scopes)))
            ):
                found.append((i, x)) if indices else found.append(x)
                if first:  # Return early for speed
                    return found[0]
        return found if not first else None

    def find_associations(
        self,
        r1: str = unset,
        kind: str = unset,
        r2: str = unset,
        t2: str = unset,
        scopes: Any = unset,
        first: bool = False,
        indices: bool = False,
    ):
        """Finds associations on this topic that matches the complete or partial arguments.
        Returns a list.
        """
        if scopes != unset:
            scopes = set(map(get_id, scopes))

        found = []
        for i, x in enumerate(self.associations):
            if (
                (r1 == unset or r1 == x.r1.id)
                and (kind == unset or kind == x.kind.id)
                and (r2 == unset or r2 == x.r2.id)
                and (t2 == unset or t2 == x.t2.id)
                and (scopes == unset or scopes <= set(map(get_id, x.scopes)))
            ):
                found.append((i, x)) if indices else found.append(x)
                if first:  # Return early for speed
                    return found[0]
        return found if not first else None

    def associations_by_r1(self, topic_dict=None):
        out = {}
        for a in self.associations:
            out.setdefault(a.r1 or "none", []).append(a)
        if topic_dict:  # TODO, we need this to cheaply look up names, but it's not very clean to pass it in here
            for ass in out.values():
                ass.sort(key=lambda a: topic_dict.get(a.t2.pk).name if a.t2.pk in topic_dict else a.t2.pk)
        return out

    def query_all_referenced_topics(self) -> QuerySet:
        topics_to_get = {self.kind.pk} if self.kind else set()
        for name in self.names:
            topics_to_get.update([t.pk for t in name.scopes])
        for occ in self.occurrences:
            topics_to_get.update([t.pk for t in occ.scopes])
            if occ.kind:
                topics_to_get.add(occ.kind.pk)
        for ass in self.associations:
            topics_to_get.update([t.pk for t in ass.scopes])
            if ass.r1.pk:
                topics_to_get.add(ass.r1.pk)
            if ass.r2.pk:
                topics_to_get.add(ass.r2.pk)
            if ass.t2.pk:
                topics_to_get.add(ass.t2.pk)
            if ass.kind.pk:
                topics_to_get.add(ass.kind.pk)

        return Topic.objects(id__in=topics_to_get)

    def add_name(self, name: str, scopes=None, index=-1):
        """ Add a new name unless equal to existing name and scopes in list.
        name -- a string
        scopes -- a list of topic id strings
        """
        if not name:
            raise ArgumentError("Expected a name, got nothing")
        if not isinstance(name, str):
            name = str(name)
        scopes = clean_scopes(scopes)

        found = self.find_names(name, scopes, first=True)
        if not found:
            if index > -1:
                self.names.insert(index, Name(name=name, scopes=scopes))
            else:
                self.names.append(Name(name=name, scopes=scopes))

    def add_occurrence(self, uri=None, content=None, kind=None, scopes=None):
        """ Add a new occurrence unless identical with existing.
        name -- a string
        scopes -- a list of topic id strings
        """
        if uri is None and content is None:
            raise ArgumentError("Need at least an URI or content")
        if kind is None:
            kind = f"{LORE_BASE}website" if uri else f"{LORE_BASE}description"

        scopes = clean_scopes(scopes)

        # If two occurrences have same uri/description/kind but different scopes, are they different?
        # Or should we just add the scope to existing?
        if not self.find_occurrences(uri, content, kind, scopes, first=True):
            self.occurrences.append(Occurrence(uri=uri, content=content, kind=kind, scopes=scopes))

    def add_association(
        self,
        t2,
        kind: str = f"{LORE_BASE}association",
        r1: str = f"{LORE_BASE}role",
        r2: str = f"{LORE_BASE}role",
        scopes: Any = None,
        two_way: bool = True,
    ):
        """ Add a new association from this topic to another topic.
        """

        scopes = clean_scopes(scopes)

        found = self.find_associations(r1=r1, kind=kind, r2=r2, t2=t2 if isinstance(t2, str) else t2.id, first=True)
        if not found:
            self.associations.append(Association(t2=t2, kind=kind, r1=r1, r2=r2, scopes=scopes,))
        if two_way:
            # Fetch reference if we got an id. Otherwise, we assume we have a Topic object already (supporting use without a database)
            if isinstance(t2, str):
                t2 = Topic.objects(id=t2).get()
            # On other topic, "this" and "other" will be reversed.
            if not t2.find_associations(r1=r2, kind=kind, r2=r1, t2=self.id, first=True):
                t2.associations.append(Association(t2=self, kind=kind, r1=r2, r2=r1, scopes=scopes,))

    def __str__(self):
        return f"{self.name} ({self.id}, {self.created_at})"

    def __repr__(self):
        return json.dumps(json.loads(self.to_json()), indent=2)


Topic.created_at.filter_options = datetime_delta_options(
    "created_at", [timedelta(days=7), timedelta(days=30), timedelta(days=365), timedelta(days=1825)]
)
# Topic.kind.filter_options = distinct_options("kind", Topic)

# instance( this_topic : r1, t2 : r2 ) / scope,scope2 or instance( this_topic, t2 )
# https://regex101.com/r/VvDDdW/1/
ltm_association_pattern = r"(.+?)\( ?(.+?)(?: ?: ?(.+?))?, ?(.+?)(?: ?: ?(.+?))? ?\)(?: ?\/ (.+))?"  # 6 capture groups
PATH_OK = SLUG_OK + "/:.@"

# LTM basics https://ontopia.net/download/ltm.html
# Topic: `[topic : instance = "Name"; "Sort-name" / norwegian @"http://www.ontopia.net/download/ltm.html"]`
# Association: `format-for(ltm : format, topic-maps : standard)`
# Occurrence: `{topic, instance, "URL"}` or `{topic, instance, [[inline data]]}`


class TopicFactory:
    # TODO change from providing topic_dict to providing function that finds Topics. In this way
    # we can use the factory both with an offline dict and an online mongodb

    # TODO handle mergers of topics due to e.g. alias.

    def __init__(
        self,
        default_bases: List[str] = None,
        default_scopes: List[str] = None,
        default_associations: List[str] = None,
        topic_dict=None,
    ):
        self.default_bases = default_bases or []
        self.topic_dict = topic_dict if topic_dict is not None else {}
        self.default_scopes = [self.basify(s) for s in (default_scopes or [])]
        self.default_associations = []
        # If we have default associations, parse a string like this
        # <instance>( this_topic : r1, t2 : r2 ) / scope,scope2
        for ass_string in default_associations or []:
            ass_kwargs = {}
            try:
                parsed_ass = extract(ass_string, ltm_association_pattern, groups=6)
                t2_id = self.basify(parsed_ass[3])
                ass_kwargs["t2"] = topic_dict.setdefault(t2_id, Topic(id=t2_id))  # Other_topic
                ass_kwargs["kind"] = self.basify(parsed_ass[0])
                if parsed_ass[2]:
                    ass_kwargs["r1"] = self.basify(parsed_ass[2])
                if parsed_ass[4]:
                    ass_kwargs["r2"] = self.basify(parsed_ass[4])
                ass_kwargs["scopes"] = self.default_scopes
                if parsed_ass[5]:
                    ass_kwargs["scopes"] += [self.basify(s) for s in parsed_ass[5].split(",")]
            except Exception as e:
                raise ValueError(f"Couldn't parse association string '{ass_string}'", e)
            self.default_associations.append(ass_kwargs)

    def basify(self, id: str) -> str:
        """Joins the ID with each string in default_bases list in order, until it matches an existing topic,
        or until it reaches the end and returns the last joined string. Returns the same ID it got if
        there are no default_bases or if the ID appear to already have a base.

        Args:
            id (str): [description]

        Raises:
            ValueError: [description]

        Returns:
            str: [description]
        """
        # We assume id with / or @ is a full domain, which implicitly excludes basifying ids that have path components but no domain
        if not id:
            raise ValueError("Empty ID given")
        elif not self.default_bases or re.match(r"^[^\/@]+@[^\/]+$", id) or "/" in id:
            return str(id)  # Already a full ID
        else:
            joined = ""
            user = "@" in id
            for base in self.default_bases:
                # if @ in id, it's a user, so id@base.com . Otherwise base.com/id
                joined = id + base.split("/", 1)[0] if user else join(base, id)
                if joined in self.topic_dict:
                    break
            return joined  # Will intentionally be the last one if none of the bases existed

    def make_topic(self, id=None, names=None, desc=None, kind=None, created_at=None) -> Topic:

        if not names:
            names = []
        if not isinstance(names, list):
            names = [names]
        if not id and names:  # Try to make an id from names
            name = names[0]
            if isinstance(name, tuple):
                name = name[0]
            if name:
                id = slugify(name, ok=PATH_OK)
        if not id:
            raise ValueError("Missing ID: no id given, no valid name given")
        else:
            id = slugify(id, ok=PATH_OK)  # Make sure ID uses valid characters

        id = self.basify(id)

        creating = False
        topic: Topic = self.topic_dict.get(id, None)
        if topic is None:
            creating = True
            topic = Topic(id=id)
            self.topic_dict[id] = topic

        if id.startswith(LORE_BASE) and not creating:
            # Don't modify LORE_BASE topics, they should always exist in correct format from before
            return topic

        # WARNING, from here on we need to be idempotent. We can get here both on a new and an existing topic,
        # and shouldn't duplicate any content not here. At the same time, we might want to update some fields.
        # Maybe would be better to split this into separate method to call on a topic explicitly.

        for n in names:
            if not n:
                continue
            if not isinstance(n, tuple):
                n = (n, [])
            name, scopes = n
            if not isinstance(scopes, list):
                scopes = [scopes]
            scopes = [self.basify(s) for s in scopes]
            scopes = scopes + self.default_scopes
            # TODO we could create an (almost empty) topic for each scope here,
            # but they could also be lazily created when accessed?
            topic.add_name(name, scopes)

        if isinstance(desc, str):
            topic.add_occurrence(content=desc, kind=f"{LORE_BASE}description", scopes=[f"{LORE_BASE}en"])

        if kind:
            # TODO we could create an (almost empty) topic for instance here,
            # but it could also be lazily created when accessed?
            topic.kind = self.basify(kind)

        if created_at and (not topic.created_at or created_at < topic.created_at):
            # Will write created_at if it's earlier than what's here
            # Todo, this will make topics with
            topic.created_at = created_at

        if creating:  # Only do first time
            for ass_kwargs in self.default_associations:
                topic.add_association(**ass_kwargs, two_way=False)

        return topic

    # A("Employment", "Employer","Employs", "Employee", "Employed by")
    def make_association(self, ass: str, role1: str, verb1: str, role2: str, verb2: str):
        role1_id, role2_id = slugify(role1, ok=PATH_OK), slugify(role2, ok=PATH_OK)
        self.make_topic(names=[ass, (verb1, role1_id), (verb2, role2_id)], kind=f"{LORE_BASE}association")
        self.make_topic(id=role1_id, names=role1, kind=f"{LORE_BASE}role")
        self.make_topic(id=role2_id, names=role2, kind=f"{LORE_BASE}role")


def create_basic_topics():
    basic_topics = {t.pk: t for t in Topic.objects(id__startswith=LORE_BASE)}

    factory = TopicFactory(default_bases=[LORE_BASE], default_scopes=["en"], topic_dict=basic_topics)
    T = factory.make_topic
    A = factory.make_association

    # Scope topics
    T("en", [("English", "en"), ("Engelska", "sv")])
    T("sv", [("Swedish", "en"), ("Svenska", "sv")])
    T("canon", "Canon content", "Denotes a characteristic as canon")
    T("contrib", "Contributed content", "Denotes a characteristic as contributed, not yet approved")
    T("community", "Community content", "Denotes a characteristic as approved community material")

    # Scope topics for names
    # sort_name, a string to used for sorting instead of basename, can also be combined with language
    # acronym, an acronym form of the base name
    # alternate, an alternate form of the base name, e.g. different spelling
    # erroneous, an incorrect alternate form of the base name

    # Occurrence instanceOfs
    T("article", "Article")
    T("audio", "Audio")
    T("bibref", "Bibliographic reference")
    T("comment", "Comment")
    T("date_of_birth", "Date of birth")
    T("date_of_death", "Date of death")
    T("started_at", "Started at")
    T("stopped_at", "Stopped at")
    T("description", "Description")
    T("gallery", "Gallery")  # This assumes to point to a normal URL that contains a gallery
    T("image", "Image")
    # Types of images:
    # Cover or Card (standing), Wide, Icon

    T("video", "Video")
    T("website", "Website")
    T("google_doc", "Google Document")  # Special GDoc occurrence to populate the article
    T("qr_code", "QR Code")
    T("prev", "Previous")
    T("next", "Next")
    T("pdf", "PDF")
    T("3d_model", "3D model")
    T("map_point", "Map point")
    T("map_geopoint", "Map geopoint")
    T("map_polygon", "Map polygon")  # Easy support for tile?
    T("map_geopolygon", "Map geopolygon")
    T("version", "Version")
    T("creator", "Creator")  # Doesn't represent the creator of other occurrences
    # Maybe topics don't have a license, the resource have a license?
    # In that case, it has to be a scope or managed at the source
    T("license", "License")

    # Correct setup of association topics
    # Create an association topic, two role topics, and
    T(
        "association",
        [("Association", "en"), ("Associated with", ["en", "role"])],
        "A plays role in an association with B, v.v.",
    )
    T("role", "Role", "A role in an association of two topics")

    # How you'd define a set of association topics without the A function
    # T(
    #     "employment",
    #     [("Employment", "en"), ("Employs", ["en", "employer"]), ("Employed by", ["en", "employee"])],
    #     kind="association",
    # )
    # T("employee", "Employee", kind="role")
    # T("employer", "Employer", kind="role")
    A("Employment", "Employer", "Employs", "Employee", "Employed by")
    A("Alternative naming", "Alias", "Alias for", "Primary", "Aliased by")
    A("Link", "Source", "Links to", "Target", "Linked from")
    A("Categorization", "Sample", "Categorized as", "Category", "Includes")
    A("Existence", "Concept", "Exists in", "World", "Embodies")
    A("Inclusion", "Part", "Part of", "Whole", "Comprises")
    A("Location", "Locale", "Located in", "Area", "Contains")
    A("Appearance", "Character", "Appears in", "Work", "Is about")
    A("Birth", "Person", "Born in", "Birthplace", "Birtplace of")
    A("Death", "Person", "Died in", "Deathplace", "Deathplace of")
    A("Killing", "Person", "Killed by", "Killer", "Killer of")
    A("Parenthood", "Child", "Child of", "Parent", "Parent of")
    A("Kinship", "Kin", "Kin of", "Kin", "Kin of")
    A("Descendance", "Descendant", "Descends from", "Ancestor", "Ancestor of")
    A("Marriage", "Spouse", "Espouses", "Spouse", "Espouses")
    A("Rulership", "Ruler", "Rules", "Demesne", "Demesne of")
    A("Membership", "Member", "Member of", "Organization", "Includes")
    # TODO handle how to write multi-lingual association assignments

    # Lore topics, all world related topics should be one of these (or a subclass of them)
    T("world", "World")  # How to link all topics to a world? It has to use a basic association. Or we use the id path?
    T("user", "User")
    T("publisher", "Publisher")
    T("agent", "Agent", "An entity that can act, such as a person, monster, god, etc")
    T("character", "Character", "A player character")
    T("domain", "Domain", "A country, state, area on a map")
    T("place", "Place", "A specific location, a point on a map")
    T("concept", "Concept", "A term or something intangible")
    T("faction", "Faction", "A group of people with an agenda")
    T("item", "Item")
    T("event", "Event")

    return basic_topics
