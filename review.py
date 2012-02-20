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

This extension adds the -p (for 'person') flag to hg push, and
requires all push requests to specify -p.  After doing the hg
push, the extension will create a new review request on kilnhg, and
set the reviewers to be those people specified with the -p flag.

The user may specify -p none to override the review functionality.
In that case, this extension will just do a normal 'hg push'.

This script requires the following fields to be set in the hg
config file (.hgrc or the like):
   kiln.prefix: the kilnhg url for this project (eg http://khanacademy.kilnhg.org)
   kiln.username: your kilnhg username (eg csilvers@khanacademy.org)
   kiln.password: your kilnhg password (eg likeidtellyou)

TODO(csilvers): compare to
https://bitbucket.org/runeh/identities/src/0f9ac5a19e48/identities.py
"""

import json
import mercurial
import urllib
import urllib2
from mercurial import commands   # needed to access mercurial.cmdutil. shrug.


class ReviewError(Exception): pass


def _slurp(url, params, post=False):
    """Fetch contents of a url-with-query-params dict (either GET or POST)."""
    params = urllib.urlencode(params, doseq=True)   # param-values can be lists
    if post:
        handle = urllib2.urlopen(url, params)
    else:
        handle = urllib2.urlopen(url + '?' + params)
    try:
    	content = handle.read()
    	return json.loads(content)
    finally:
        handle.close()


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
    'hg review' commandline if present, or 'default-push' or
    'default'.

    Arguments:
        repo: the hg repository being used.  We need it only for its config.
        preferred_repo: the argument to 'hg review', or None if no
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
       ReviewError if no person-record is found for any of the reviewers.
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
            raise ReviewError('No reviewer found matching "%s"' % reviewer)
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
    return None


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


cmdtable = {}
command = mercurial.cmdutil.command(cmdtable)


@command('review|scrutinize',
        [('t', 'title', '', 'use text as default title for code review', 'TITLE'),
         ('c', 'comment', '', 'use text as default comment for code review', 'COMMENT'),
         ('r', 'revs', [], 'revisions for review, otherwise defaults to "tip"', 'REV'),
         ('p', 'people', [], 'people to include in the review, comma separated, or "none" for no review', 'REVIEWERS'),
         ('e', 'editor', False, 'invoke your editor for default comment')],
         'hg review [-t TITLE] [-e | -c COMMENT] [-p PEOPLE] [-r REV] [repo]')
def review(ui, repo, *dest, **opts):
    """create a code review for some changesets on kiln

    Review creates a brand new code review on kiln for a changeset on kiln.
    If no revision is specified, the code review defaults to the most recent
    changeset.

    Specify people to peek at your review by passing a comma-separated list
    of people to review your code, by passing multiple -p flags, or both.
      hg review -p tim,alex,ben -p joey

    You can specify revisions by passing a hash-range,
      hg review -r 13bs32abc:tip
    or by passing individual changesets
      hg review -r 75c471319a5b -r 41056495619c

    Using -e will open up your favorite editor and includes all the changeset
    descriptions for any revisions selected as the code review comment.
    """
    url_prefix = repo.ui.config('auth', 'kiln.prefix')
    if url_prefix is None:
        ui.warn("In order to work, in your hgrc please set:\n\n")
        ui.warn("[auth]\n")
        ui.warn("kiln.prefix = https://<kilnrepo>.kilnhg.com\n")
        ui.warn("kiln.username = <username>@<domain>.com\n")
        ui.warn("kiln.password = <password>\n")
        return 0

    review_params = {}

    auth_token = _get_authtoken()
    review_params['token'] = auth_token

    # -t: title
    if opts.get('title'):
        review_params['sTitle'] = opts.get('title')

    # -c: comment
    if opts.get('title'):
        review_params['sDescription'] = opts.get('comment')

    # -r: revs
    if opts.get('revs'):
        revs = opts['revs']
        changesets = [repo[rev].hex()[:12]
                      for rev in mercurial.scmutil.revrange(repo, revs)]
    else:
        # TODO(csilvers): have it be all changesets in 'hg outgoing'
        changesets = ['tip']
    review_params['revs'] = changesets

    # -p: people
    if opts.get('people') != ['none']:
        reviewers = _get_reviewers(ui, auth_token, opts.get('people'))
        review_params['ixReviewers'] = [r['ixPerson'] for r in reviewers]

    # -e: editor
    if opts.get('editor'):
         # If -c was also specified, default the editor-text to that.
	 # Otherwise, use the text from the changesets being reviewed.
         if 'sDescription' in review_params:
	     default_comment = review_params['sDescription']
	 else:
             changeset_descs = [repo[rev].description() for rev in changesets]
             default_comment = "\n".join(changeset_descs)
         current_user = (repo.ui.config('auth', 'kiln.username') or
	                 repo.ui.config('ui', 'username'))
         review_params['sDescription'] = ui.edit(default_comment, current_user)

    # dest is the commandline argument.  Only one should be specified.
    if dest:
        dest = dest[0]

    repo_url_to_push_to = _get_repo_to_push_to(repo, dest)
    # TODO(csilvers): what is the right thing to do if this returns None?
    review_params['ixRepo'] = _get_repo_index_for_repo_url(repo, auth_token,
                                                           repo_url_to_push_to)

    review_status = _make_review(review_params)
    if review_status:
        if 'ixReview' not in review_status:
           ui.status('Error creating review: %s\n' % review_status)
	   return 0
        ui.status('Review created!\n')
        ui.status('%s/Review/%s\n' % (url_prefix, review_status['ixReview']))
        return 1
    else:
        return 0
