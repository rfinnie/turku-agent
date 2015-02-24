#!/usr/bin/env python

from distutils.core import setup

setup(
    name='turku_agent',
    description='Turku backups - client agent',
    author='Ryan Finnie',
    author_email='ryan.finnie@canonical.com',
    url='https://launchpad.net/turku',
    scripts=['turku-agent-ping', 'turku-update-config'],
)
