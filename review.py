# codereview.py
#
# Author: Craig Silverstein <csilvers@khanacademy.org>
#
# Based on code that is
# Copyright Marcos Ojeda <marcos@khanacademy.org> on 2012-01-23.
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.


"""Wrap 'hg push' to create a code review in Kiln.

This extension adds the --rr (for 'review reviewer') flag to hg push,
and requires all push requests to specify --rr.  After doing the hg
push, the extension will create a new review request on kilnhg, and
set the reviewers to be those people specified with the --rr flag.

The user may specify --rr none to override the review functionality.
In that case, this extension will just do a normal 'hg push'.

This script requires the following fields to be set in the hg
config file (.hgrc or the like):
   [auth]
   kiln.prefix: the kilnhg url for this project (eg http://khanacademy.kilnhg.org)
   kiln.username: your kilnhg username (eg csilvers@khanacademy.org)
   kiln.password: your kilnhg password (eg likeidtellyou)
"""

import json
import mercurial.cmdutil
import mercurial.commands
import mercurial.extensions
import mercurial.hg
import mercurial.node
import mercurial.scmutil
import mercurial.ui
import mercurial.util
import urllib
import urllib2


def _slurp(url, params, post=False):
    """Fetch contents of a url-with-query-params dict (either GET or POST)."""
    try:
        params = urllib.urlencode(params, doseq=True)   # param-vals can be lists
        if post:
            handle = urllib2.urlopen(url, params)
        else:
            handle = urllib2.urlopen(url + '?' + params)
        try:
            content = handle.read()
        finally:
            handle.close()
    except urllib2.URLError, why:
        # It would be nice to show params too, but they may contain a password.
        raise mercurial.util.Abort('Error communicating with kilnhg:'
                                   ' url "%s", error "%s"' % (url, why))
    return json.loads(content)


# via https://developers.fogbugz.com/default.asp?W157
def _kiln_url(command):
    """Create a kiln api call to your kiln project, for the given command."""
    url_prefix = mercurial.ui.ui().config('auth', 'kiln.prefix')
    return '%s/Api/1.0/%s' % (url_prefix, command)


def _slurp_from_kiln(command, params, post=False):
    """Create a kiln url from command, and fetches its contents."""
    return _slurp(_kiln_url(command), params, post)


def _get_authtoken():
    """Returns credentials for accessing the kilnhg website."""
    # TODO(csilvers): just do this once and store the token, not the password
    username = mercurial.ui.ui().config('auth', 'kiln.username')
    password = mercurial.ui.ui().config('auth', 'kiln.password')
    return _slurp_from_kiln('Auth/Login',
                            {'sUser': username, 'sPassword': password})


def _get_repo_to_push_to(repo, preferred_repo):
    """Of all the repositories in the user's path, return the one to push to.

    The 'best' if the passed-in preferred_repo, which comes from the
    'hg push' commandline if present, or 'default-push' or
    'default'.

    Arguments:
        repo: the hg repository being used.  We need it only for its config.
        preferred_repo: the argument to 'hg push', or None if no
           argument is given.  This is the same as the DEST argument
           to 'hg push'.
    """
    # We do all case-insensitive comparisons here, and convert to a dict.
    repos = dict((x.lower(), y.lower())
                 for (x,y) in repo.ui.configitems('paths'))
    if preferred_repo:
        return repos.get(preferred_repo.lower(), None)
    if 'default-push' in repos:
        return repos['default-push']
    if 'default' in repos:
        return repos['default']
    return None


def _get_reviewers(ui, auth_token, reviewers):
    """Given a list of desired reviewers, return a list of kiln people objects.

    The reviewers are specified as a list of 'names', where a name is
    a subset of either the perons's physical name as recorded on the
    kilnhg site, or their email address as recorded on the kilnhg
    site.

    This function downloads a list of all the kiln 'person records'
    that are visible to the current user, and for each specified
    reviewer, finds the corresponding 'person record' for it.  In
    the case of ambiguity, it presents the user with a choice.

    Arguments:
      ui: the hg-to-console ui element
      auth_token: the token used to authenticate the user, from _get_authtoken
      reviewers: a list of reviewer-names, as described above.  Each
        element of the list can also be a comma-separated list of
        names, for instance [ 'tom', 'dick,harry' ]

    Returns:
      A set of kiln people records, one for each reviewer specified
      in reviewers.  A peopel record has an 'sName, 'sEmail', and
      'ixPerson' field.

    Raises:
       Abort if no person-record is found for any of the reviewers.
    """
    all_people = _slurp_from_kiln('Person', {'token': auth_token})

    # Convert the list to a set, dealing with commas as we go.
    all_reviewers = set()
    for reviewer_entry in reviewers:
        for one_review in reviewer_entry.split(','):
           all_reviewers.add(one_review.strip().lower())

    # For each asked-for reviewer, find the set of people records that
    # reviewer could be referring to.  Hopefully it's exactly one!
    disambiguated_reviewers = {}  # map from email (unique id) to person-record
    for reviewer in all_reviewers:
        candidate_reviewers = []  # all people whose name match 'reviewer'
        for person in all_people:
            if (reviewer in person["sName"].lower() or
                reviewer in person["sEmail"].lower()):
               candidate_reviewers.append(person)

        if not candidate_reviewers:   # no person matched the reviewer
            raise mercurial.util.Abort('No reviewer found matching "%s"'
                                       % reviewer)
        elif len(candidate_reviewers) > 1:
            ui.status('\nHmm...There are a few folks matching "%s"\n' % reviewer)
            choices = ['%s. %s (%s)\n' % (i+1, p['sName'], p['sEmail'])
                       for (i, p) in enumerate(candidate_reviewers)]
            for choice in choices:
                ui.status(choice)
            pick = ui.promptchoice('Which "%s" did you mean?' % reviewer,
                                   ["&" + c for c in choices])
            picked_reviewer = candidate_reviewers[pick]
        else:
            picked_reviewer = candidate_reviewers[0]
        disambiguated_reviewers[picked_reviewer['sEmail']] = picked_reviewer

    return disambiguated_reviewers.values()


def _get_repo_index_for_repo_url(repo, auth_token, repo_url):
    """For a given repository, return its ixRepo, or None if not readable."""
    url_prefix = repo.ui.config('auth', 'kiln.prefix')
    all_projects = _slurp_from_kiln("Project", {'token': auth_token})
    for project in all_projects:
        for repo_group in project['repoGroups']:
            for repo in repo_group['repos']:
                url = '%s/code/%s/%s/%s' % (url_prefix, repo['sProjectSlug'],
                                            repo['sGroupSlug'], repo['sSlug'])
                if url.lower() == repo_url.lower():
                    return repo['ixRepo']
    raise mercurial.util.Abort('No repository found matching %s' % repo_url)


def _make_review(params):
    """Create a review on the hgkiln website.

    Arguments:
      params: the parameters to the "create review" API call.
              See https://developers.fogbugz.com/default.asp?W167
              We use ixReviewers, ixRepo, maybe revs and title and desc.

    Returns:
      The return message from the "create review" API call: None on
      failure, and <something else> on success.
    """
    return _slurp_from_kiln("Review/Create", params, post=True)


def push_with_review(origfn, ui, repo, *args, **opts):
    """overrides 'hg push' to add creating a code review for the push on kiln.

    Review creates a brand new code review on kiln for a changeset on kiln.
    If no revision is specified, the code review defaults to the most recent
    changeset.

    Specify people to peek at your review by passing a comma-separated list
    of people to review your code, by passing multiple --rr flags, or both.
      hg push --rr tim,alex,ben --rr joey

    You can specify revisions by passing a hash-range,
      hg push --rrev 13bs32abc:tip
    or by passing individual changesets
      hg push --rrev 75c471319a5b --rrev 41056495619c

    Using --reditor will open up your favorite editor and includes all
    the changeset descriptions for any revisions selected as the code
    review comment.

    All the flags supported by 'hg push' are passed through to push.
    """
    # First order of business: If the user passed in --rr none, just
    # fall back onto the native push.
    if opts.get('rr') == ['none']:
        return origfn(ui, repo, *args, **opts)

    url_prefix = repo.ui.config('auth', 'kiln.prefix')
    if url_prefix is None:
        ui.warn("In order to work, in your hgrc please set:\n\n")
        ui.warn("[auth]\n")
        ui.warn("kiln.prefix = https://<kilnrepo.kilnhg.com>\n")
        ui.warn("kiln.username = <username>@<domain>.com\n")
        ui.warn("kiln.password = <password>\n")
        return 0

    # dest is the commandline argument.  At most one should be specified.
    dest = None
    if args:
        if len(args) > 1:
            raise mercurial.util.Abort('At most one dest should be specified.')
        dest = args[0]

    review_params = {}

    auth_token = _get_authtoken()
    review_params['token'] = auth_token

    # -rtitle: title
    title = opts.pop('rtitle', None)
    if title:
        review_params['sTitle'] = title

    # -rcomment: comment
    comment = opts.pop('rcomment', None)
    if comment:
        review_params['sDescription'] = comment

    # -rrev: revs
    revs = opts.pop('rrev', None)
    if revs:
        changesets = [repo[rev].hex()[:12]
                      for rev in mercurial.scmutil.revrange(repo, revs)]
    else:
        # TODO(csilvers): don't use an internal function from hg.
        changeset_nodes = mercurial.hg._outgoing(ui, repo, dest, {})
        if not changeset_nodes:
            raise mercurial.util.Abort('No changesets found to push/review. Use'
                                       ' --rrev to specify changesets manually.')
        changesets = [mercurial.node.hex(n)[:12] for n in changeset_nodes]
    review_params['revs'] = changesets

    # -rr: people
    people = opts.pop('rr', None)
    if not people:
        raise mercurial.util.Abort('Must specify at least one reviewer via -rr.'
                                   '  Pass "-rr none" to bypass review.')
    assert people != ['none']   # should have been checked above
    reviewers = _get_reviewers(ui, auth_token, people)
    review_params['ixReviewers'] = [r['ixPerson'] for r in reviewers]

    # -e: editor
    editor = opts.pop('editor', None)
    if editor:
         # If -rcomment was also specified, default the editor-text to that.
         # Otherwise, use the text from the changesets being reviewed.
         if 'sDescription' in review_params:
             default_comment = review_params['sDescription']
         else:
             changeset_descs = [repo[rev].description() for rev in changesets]
             default_comment = "\n".join(changeset_descs)
         current_user = (repo.ui.config('auth', 'kiln.username') or
                         repo.ui.config('ui', 'username'))
         review_params['sDescription'] = ui.edit(default_comment, current_user)

    repo_url_to_push_to = _get_repo_to_push_to(repo, dest)
    review_params['ixRepo'] = _get_repo_index_for_repo_url(repo, auth_token,
                                                           repo_url_to_push_to)

    # First do the push, then do the review.
    origfn(ui, repo, *args, **opts)

    ui.status('Creating review...')
    review_status = _make_review(review_params)
    assert review_status, 'Kiln API is returning None??'
    if 'ixReview' not in review_status:
        ui.status('FAILED: %s\n' % review_status)
        return 0
    ui.status('done!\n')
    ui.status('%s/Review/%s\n' % (url_prefix, review_status['ixReview']))
    return 1


def uisetup(ui):
    """The magic command to set up pre-hg hooks.  We override 'hg push'."""
    entry = mercurial.extensions.wrapcommand(mercurial.commands.table, 'push',
                                             push_with_review)
    extra_opts = [
                  ('', 'rr', [],
                   ('people to include in the review, comma separated,'
                    ' or "none" for no review')),
                  ('', 'rrev', [],
                   'revisions for review, otherwise defaults to `hg outgoing`'),
                  ('', 'rtitle', '',
                   'use text as default title for code review'),
                  ('', 'rcomment', '',
                   'use text as default comment for code review'),
                  ('', 'reditor', False,
                   'invoke your editor to input the code review comment'),
                  ]
    entry[1].extend(extra_opts)
