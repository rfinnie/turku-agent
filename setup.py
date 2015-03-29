#!/usr/bin/env python

# Turku backups - client agent
# Copyright 2015 Canonical Ltd.
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3, as published by
# the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranties of MERCHANTABILITY,
# SATISFACTORY QUALITY, or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program.  If not, see <http://www.gnu.org/licenses/>.

from distutils.core import setup


VERSION = '0.1.0'


def get_bzr_revno():
    try:
        import bzrlib.errors
        from bzrlib.branch import Branch
    except ImportError:
        return 0
    import os
    try:
        branch = Branch.open(os.path.dirname(__file__))
    except bzrlib.errors.NotBranchError:
        return 0
    return branch.last_revision_info()[0]


v = VERSION.split('.')
if int(v[1]) % 2 == 1:
    VERSION = '.'.join([v[0], v[1], str(get_bzr_revno())])

setup(
    name='turku_agent',
    description='Turku backups - client agent',
    version=VERSION,
    author='Ryan Finnie',
    author_email='ryan.finnie@canonical.com',
    url='https://launchpad.net/turku',
    packages=['turku_agent'],
    scripts=[
        'turku-agent-ping', 'turku-agent-rsyncd-wrapper',
        'turku-update-config',
    ],
)
