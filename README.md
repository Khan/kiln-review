# kiln-review

This is a mercurial extension that allows you to create code reviews on kiln right after you commit/push them on the command line.

The general gist of this is that you can create a code review via `hg` by doing something like this:

    hg push --rr tim,alex --rr joe

to get alex, tim and joe to review your feature. 

A more full featured example would be this:

    hg push --rrev ac39a0212:tip --rr ben --rtitle "awesome feature" --reditor

which creates a code review for the range of commits between
ac39... and tip. it assigns ben to review the code and sets the title
to "awesome feature" and then pops open vim to edit the default
comment for the code review.

## Installing

save the `review.py` wherever and in your `~/.hgrc` or just repo/.hg/hgrc add

    [extensions]
    dummy = /path/to/review.py

you'll need to add the following as well to your hgrc file (use your
actual name and password instead):

    [auth]
    kiln.prefix = https://kilnrepo.kilnhg.com
    kiln.username = tim@kilnorg.com
    kiln.password = keymash

yeah, that's a little gross, but those are the breaks.

you will also need to have the "Mercurial" python package installed:
   http://pypi.python.org/pypi/Mercurial/0.9

or you may `pip install mercurial`

## Using

```
overrides 'hg push' to add creating a code review for the push on kiln.

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
```
