#!/usr/bin/env python3

# Turku backups - client agent
# Copyright (C) 2015-2020 Canonical Ltd., Ryan Finnie and other contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
from setuptools import setup


def read(filename):
    with open(os.path.join(os.path.dirname(__file__), filename), encoding="utf-8") as f:
        return f.read()


setup(
    name="turku_agent",
    description="Turku backups - client agent",
    long_description=read("README.md"),
    long_description_content_type="text/markdown",
    author="Ryan Finnie",
    url="https://github.com/rfinnie/turku-agent",
    python_requires="~=3.4",
    packages=["turku_agent"],
    install_requires=["requests"],
    entry_points={
        "console_scripts": [
            "turku-agent-ping = turku_agent.ping:main",
            "turku-update-config = turku_agent.update_config:main",
        ]
    },
)
