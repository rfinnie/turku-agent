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

import logging
import os

from .utils import load_config


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--config-dir", "-c", type=str, default="/etc/turku-agent")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--detach", action="store_true")
    return parser.parse_known_args()


def main():
    args, rest = parse_args()

    logging.basicConfig(level=(logging.DEBUG if args.debug else logging.INFO))

    config = load_config(args.config_dir)
    rsyncd_command = config["rsyncd_command"]
    if not args.detach:
        rsyncd_command.append("--no-detach")
    rsyncd_command.append("--daemon")
    rsyncd_command.append(
        "--config=%s" % os.path.join(config["var_dir"], "rsyncd.conf")
    )
    rsyncd_command += rest
    logging.debug("Executing: {}".format(rsyncd_command))
    os.execvp(rsyncd_command[0], rsyncd_command)
