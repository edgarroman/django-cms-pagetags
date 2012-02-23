import re

from django import template
from django.conf import settings
from django.db.models import Q
from cms.models import Page

from tagging.models import TaggedItem
from tagging.utils import parse_tag_input
from pagetags.models import PageTagging
import shlex

register = template.Library()


class StaticSlug(unicode):
    def bind(self, context):
        pass


class DynamicSlug():
    def __init__(self, varname):
        self.varname = varname
        self._value = u''

    def bind(self, context):
        self._value = context[self.varname]

    def __unicode__(self):
        return self._value


class StaticTags(list):
    def __init__(self, tags):
        super(StaticTags, self).__init__(parse_tag_input(tags))

    def bind(self, context):
        pass


class DynamicTags(list):
    def __init__(self, varname):
        self.varname = varname
        super(DynamicTags, self).__init__()

    def bind(self, context):
        self[:] = parse_tag_input(context.get(self.varname, ''))

class TagsOfTaggedPagesNode(template.Node):
    def __init__(self, parsed_tags, var_name):
        self.parsed_tags = parsed_tags
        self.var_name = var_name

    def render(self, context):
        self.parsed_tags.bind(context)
        pagetaggings_from_site = PageTagging.objects.filter(
            page__site=settings.SITE_ID
        ).select_related()

        page_taggings = TaggedItem.objects.get_by_model(
            pagetaggings_from_site, self.parsed_tags
        )

        # now loop through the pages found and extract the tags
        final_tag_list = []
        for page_tagging in page_taggings:
            # We'll have to ensure that we only have one instance of
            # each tag
            page_tag_list = shlex.split(page_tagging.page_tags)
            for tag in page_tag_list:
                final_tag_list.append(tag)

        # We use a set here to remove duplicate tags
        context[self.var_name] =  set(final_tag_list)
        return ''


class TaggedPagesNode(template.Node):
    def __init__(self, parsed_tags, ordering, limit, var_name):
        self.parsed_tags = parsed_tags
        self.ordering = ordering or 'chronological'
        self.limit = limit
        self.var_name = var_name

    def render(self, context):
        self.parsed_tags.bind(context)
        pagetaggings_from_site = PageTagging.objects.filter(
            page__site=settings.SITE_ID
        ).select_related()

        if self.ordering == 'alphabetical':
            pagetaggings_from_site = pagetaggings_from_site.order_by(
                'page__title_set__title'
            )

        if self.ordering == 'chronological':
            pagetaggings_from_site = pagetaggings_from_site.order_by(
                '-page__publication_date'
            )

        page_taggings = TaggedItem.objects.get_by_model(
            pagetaggings_from_site, self.parsed_tags
        )[:self.limit]

        context[self.var_name] = [
            page_tagging.page for page_tagging in page_taggings
        ]
        return ''


class SimilarPagesNode(template.Node):
    def __init__(self, parsed_slug, limit, var_name):
        self.parsed_slug = parsed_slug
        self.limit = limit
        self.var_name = var_name

    def render(self, context):
        self.parsed_slug.bind(context)
        try:
            page = Page.objects.get(
                Q(site=settings.SITE_ID),
                Q(title_set__slug=self.parsed_slug)
            )
        except Page.DoesNotExist:
            return ''

        page_taggings = TaggedItem.objects.get_related(
            page.pagetagging,
            PageTagging.objects.select_related().filter(
                page__site=settings.SITE_ID
            )
        )[:self.limit]
        context[self.var_name] = [
            page_tagging.page for page_tagging in page_taggings
        ]
        return ''


def _extract_tag_content(token):
    try:
        tag_name, arg = token.contents.split(None, 1)
    except ValueError:
        raise template.TemplateSyntaxError(
            "%r tag requires additional arguments" % token.contents.split()[0]
        )
    return tag_name, arg


def parsed_tags(quoted_or_var):
    if (quoted_or_var[0] == quoted_or_var[-1]
    and quoted_or_var[0] in ('"', "'")):
        return StaticTags(tags=quoted_or_var[1:-1])
    return DynamicTags(varname=quoted_or_var)


def parse_slug(quoted_or_var):
    if (quoted_or_var[0] == quoted_or_var[-1]
    and quoted_or_var[0] in ('"', "'")):
        return StaticSlug(quoted_or_var[1:-1])
    return DynamicSlug(varname=quoted_or_var)

@register.tag
def tags_of_pages_with_tags(parser, token):
    '''
    This tag returns a list of tags
    the tags returns are the union of all tags for any page with
    the requested tag.  So if a page is tagged with:
      blog, art, motorcycle
    then a call to tags_of_pages_with_tags 'blog' will return:
      blog, art, motorcycle
    '''
    parsed_tags, var_name = _parse_tags_of_pages_with_tags(token)
    return TagsOfTaggedPagesNode(parsed_tags, var_name)

def _parse_tags_of_pages_with_tags(token):
    tag_name, arg = _extract_tag_content(token)

    m = re.search(r"""
        (.*?)                                   # taglist or varname

        \s+ as \s+ (\w+) \s*                    # collector variable
    """, arg, flags=re.VERBOSE)
    if not m:
        raise template.TemplateSyntaxError(
            "%r tag had invalid arguments" % tag_name
        )
    tags, var_name = m.groups()

    return parsed_tags(tags), var_name


@register.tag
def pages_with_tags(parser, token):
    parsed_tags, ordering, limit, var_name = _parse_pages_with_tags(token)
    return TaggedPagesNode(parsed_tags, ordering, limit, var_name)


def _parse_pages_with_tags(token):
    tag_name, arg = _extract_tag_content(token)

    m = re.search(r"""
        (.*?)                                   # taglist or varname

        (\s+ order                              # optional order clause
            \s+(alphabetical|chronological)     # order combinations
        )*

        (\s+ limit \s+ (\d+))*                  # optional limit clause

        \s+ as \s+ (\w+) \s*                    # collector variable
    """, arg, flags=re.VERBOSE)

    if not m:
        raise template.TemplateSyntaxError(
            "%r tag had invalid arguments" % tag_name
        )
    tags, _, ordering, _, limit, var_name = m.groups()

    return parsed_tags(tags), ordering, limit and int(limit), var_name


@register.tag
def pages_similar_with(parser, token):
    parsed_slug, limit, var_name = _parse_pages_similar_with(token)
    return SimilarPagesNode(parsed_slug, limit, var_name)


def _parse_pages_similar_with(token):
    tag_name, arg = _extract_tag_content(token)

    m = re.search(r"""
        (.*?)                                   # slug or varname

        (\s+ limit \s+ (\d+))*                  # optional limit clause

        \s+ as \s+ (\w+) \s*                    # collector variable
    """, arg, flags=re.VERBOSE)
    if not m:
        raise template.TemplateSyntaxError(
            "%r tag had invalid arguments" % tag_name
        )
    page_slug, _, limit, var_name = m.groups()

    return parse_slug(page_slug), limit and int(limit), var_name
