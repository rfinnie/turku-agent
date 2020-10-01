#!/usr/bin/env python3

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

import sys
from setuptools import setup

assert sys.version_info > (3, 4)


setup(
    name="turku_agent",
    description="Turku backups - client agent",
    version="0.2.0",
    author="Ryan Finnie",
    author_email="ryan.finnie@canonical.com",
    url="https://launchpad.net/turku",
    packages=["turku_agent"],
    install_requires=["requests"],
    entry_points={
        "console_scripts": [
            "turku-agent-ping = turku_agent.ping:main",
            "turku-update-config = turku_agent.update_config:main",
        ]
    },
)
