# SPDX-PackageSummary: Turku backups - client agent
# SPDX-FileCopyrightText: Copyright (C) 2015-2020 Canonical Ltd.
# SPDX-FileCopyrightText: Copyright (C) 2015-2021 Ryan Finnie <ryan@finnie.org>
# SPDX-License-Identifier: GPL-3.0-or-later

import copy
import errno
import fcntl
import json
import logging
import os
import platform
import random
import string
import subprocess
import sys
import urllib.parse
import uuid

import requests

try:
    import yaml
except ImportError as e:
    yaml = e


class RuntimeLock:
    filename = None
    fh = None

    def __init__(self, name=None, lock_dir=None):
        if name is None:
            if sys.argv[0]:
                name = os.path.basename(sys.argv[0])
            else:
                name = __name__
        if lock_dir is None:
            for dir in ("/run/lock", "/var/lock", "/run", "/var/run", "/tmp"):
                if os.path.exists(dir):
                    lock_dir = dir
                    break
            if lock_dir is None:
                raise FileNotFoundError("Suitable lock directory not found")
        filename = os.path.join(lock_dir, "{}.lock".format(name))

        # Do not set fh to self.fh until lockf/flush/etc all succeed
        fh = open(filename, "w")
        try:
            fcntl.lockf(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError as e:
            if e.errno in (errno.EACCES, errno.EAGAIN):
                raise
        fh.write("%10s\n" % os.getpid())
        fh.flush()
        fh.seek(0)

        self.fh = fh
        self.filename = filename

    def close(self):
        if self.fh:
            self.fh.close()
            self.fh = None
            os.unlink(self.filename)

    def __del__(self):
        self.close()

    def __enter__(self):
        self.fh.__enter__()
        return self

    def __exit__(self, exc, value, tb):
        result = self.fh.__exit__(exc, value, tb)
        self.close()
        return result


def json_dump_p(obj, f):
    """Calls json.dump with standard (pretty) formatting"""
    return json.dump(obj, f, sort_keys=True, indent=4, separators=(",", ": "))


def config_load_file(file):
    """Load and return a .json or (if available) .yaml configuration file"""
    with open(file) as f:
        try:
            if file.endswith(".yaml") and not isinstance(yaml, ImportError):
                return yaml.safe_load(f)
            else:
                return json.load(f)
        except Exception:
            raise ValueError("Error loading {}".format(file))


def dict_merge(s, m):
    """Recursively merge one dict into another."""
    if not isinstance(m, dict):
        return m
    out = copy.deepcopy(s)
    for k, v in list(m.items()):
        if k in out and isinstance(out[k], dict):
            out[k] = dict_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def safe_write(file, **kwargs):
    """(Try to) safely write files with minimum collision possibility"""

    def _sw_close(fh):
        if fh.closed:
            return
        fh._fh_close()
        os.rename(fh.name, fh.original_name)

    if "mode" not in kwargs:
        kwargs["mode"] = "x"
    temp_name = "{}.tmp{}~".format(file, str(uuid.uuid4()))
    fh = open(temp_name, **kwargs)
    setattr(fh, "original_name", file)
    setattr(fh, "_fh_close", fh.close)
    setattr(fh, "close", lambda: _sw_close(fh))
    return fh


def load_config(config_dir):
    config = {}
    config["config_dir"] = config_dir

    config_d = os.path.join(config["config_dir"], "config.d")
    sources_d = os.path.join(config["config_dir"], "sources.d")

    # Merge in config.d files to the root level
    config_files = []
    if os.path.isdir(config_d):
        config_files = [
            os.path.join(config_d, fn)
            for fn in os.listdir(config_d)
            if (
                fn.endswith(".json")
                or (fn.endswith(".yaml") and not isinstance(yaml, ImportError))
            )
            and os.path.isfile(os.path.join(config_d, fn))
            and os.access(os.path.join(config_d, fn), os.R_OK)
        ]
    config_files.sort()
    for file in config_files:
        config = dict_merge(config, config_load_file(file))

    if "var_dir" not in config:
        config["var_dir"] = "/var/lib/turku-agent"

    var_config_d = os.path.join(config["var_dir"], "config.d")

    # Load /var config.d files (.json only)
    var_config = {}
    var_config_files = []
    if os.path.isdir(var_config_d):
        var_config_files = [
            os.path.join(var_config_d, fn)
            for fn in os.listdir(var_config_d)
            if fn.endswith(".json")
            and os.path.isfile(os.path.join(var_config_d, fn))
            and os.access(os.path.join(var_config_d, fn), os.R_OK)
        ]
    var_config_files.sort()
    for file in var_config_files:
        var_config = dict_merge(var_config, config_load_file(file))
    # /etc gets priority over /var
    var_config = dict_merge(var_config, config)
    config = var_config

    if "lock_dir" not in config:
        config["lock_dir"] = None  # Determine automatically

    if "rsyncd_command" not in config:
        config["rsyncd_command"] = ["rsync"]

    # "*" automatically determines group in recent rsyncd.
    # macOS's outdated rsync will need "wheel"
    if "rsyncd_group" not in config:
        config["rsyncd_group"] = "*"

    if "rsyncd_local_address" not in config:
        config["rsyncd_local_address"] = "127.0.0.1"

    if "rsyncd_user" not in config:
        config["rsyncd_user"] = "root"

    if "ssh_command" not in config:
        config["ssh_command"] = ["ssh"]

    if "ssh_key_type" not in config:
        config["ssh_key_type"] = "ed25519"

    # If a go/no-go program is defined, run it and only go if it exits 0.
    # Type: String (program with no args) or list (program first, optional arguments after)
    if "gonogo_program" not in config:
        config["gonogo_program"] = None

    var_sources_d = os.path.join(config["var_dir"], "sources.d")

    # Validate the unit name
    if "unit_name" not in config:
        config["unit_name"] = platform.node()
        # If this isn't in the on-disk config, don't write it; just
        # generate it every time

    # Pull the SSH public key
    if os.path.isfile(os.path.join(config["var_dir"], "ssh_key.pub")):
        with open(os.path.join(config["var_dir"], "ssh_key.pub")) as f:
            config["ssh_public_key"] = f.read().rstrip()
        config["ssh_public_key_file"] = os.path.join(config["var_dir"], "ssh_key.pub")
        config["ssh_private_key_file"] = os.path.join(config["var_dir"], "ssh_key")

    sources_config = {}
    # Merge in sources.d files to the sources dict
    # (.json/.yaml in /etc, .json only in /var)
    sources_directories = []
    if os.path.isdir(var_sources_d):
        sources_directories.append(var_sources_d)
    if os.path.isdir(sources_d):
        sources_directories.append(sources_d)
    for directory in sources_directories:
        for file in sorted(
            [
                os.path.join(directory, fn)
                for fn in os.listdir(directory)
                if (
                    fn.endswith(".json")
                    or (
                        fn.endswith(".yaml")
                        and directory == sources_d
                        and not isinstance(yaml, ImportError)
                    )
                )
                and os.path.isfile(os.path.join(directory, fn))
                and os.access(os.path.join(directory, fn), os.R_OK)
            ]
        ):
            sources_config = dict_merge(sources_config, config_load_file(file))

    # Check for required sources options
    for s in list(sources_config.keys()):
        if "path" not in sources_config[s]:
            del sources_config[s]

    config["sources"] = sources_config

    return config


def fill_config(config):
    config_d = os.path.join(config["config_dir"], "config.d")
    sources_d = os.path.join(config["config_dir"], "sources.d")
    var_config_d = os.path.join(config["var_dir"], "config.d")
    var_sources_d = os.path.join(config["var_dir"], "sources.d")

    # Create required directories
    for d in (config_d, sources_d, var_config_d, var_sources_d):
        if not os.path.isdir(d):
            os.makedirs(d)

    # Validate the machine UUID/secret
    write_uuid_data = False
    if "machine_uuid" not in config:
        config["machine_uuid"] = str(uuid.uuid4())
        write_uuid_data = True
    if "machine_secret" not in config:
        config["machine_secret"] = "".join(
            random.choice(string.ascii_letters + string.digits) for i in range(30)
        )
        write_uuid_data = True
    # Write out the machine UUID/secret if needed
    if write_uuid_data:
        with safe_write(os.path.join(var_config_d, "10-machine_uuid.json")) as f:
            os.fchmod(f.fileno(), 0o600)
            json_dump_p(
                {
                    "machine_uuid": config["machine_uuid"],
                    "machine_secret": config["machine_secret"],
                },
                f,
            )

    # Restoration configuration
    write_restore_data = False
    if "restore_path" not in config:
        config["restore_path"] = "/var/backups/turku-agent/restore"
        write_restore_data = True
    if "restore_module" not in config:
        config["restore_module"] = "turku-restore"
        write_restore_data = True
    if "restore_username" not in config:
        config["restore_username"] = str(uuid.uuid4())
        write_restore_data = True
    if "restore_password" not in config:
        config["restore_password"] = "".join(
            random.choice(string.ascii_letters + string.digits) for i in range(30)
        )
        write_restore_data = True
    if write_restore_data:
        with safe_write(os.path.join(var_config_d, "10-restore.json")) as f:
            os.fchmod(f.fileno(), 0o600)
            restore_out = {
                "restore_path": config["restore_path"],
                "restore_module": config["restore_module"],
                "restore_username": config["restore_username"],
                "restore_password": config["restore_password"],
            }
            json_dump_p(restore_out, f)
    if not os.path.isdir(config["restore_path"]):
        os.makedirs(config["restore_path"])

    # Generate the SSH keypair if it doesn't exist
    if "ssh_private_key_file" not in config:
        subprocess.check_call(
            [
                "ssh-keygen",
                "-t",
                config["ssh_key_type"],
                "-N",
                "",
                "-C",
                "turku",
                "-f",
                os.path.join(config["var_dir"], "ssh_key"),
            ]
        )
        with open(os.path.join(config["var_dir"], "ssh_key.pub")) as f:
            config["ssh_public_key"] = f.read().rstrip()
        config["ssh_public_key_file"] = os.path.join(config["var_dir"], "ssh_key.pub")
        config["ssh_private_key_file"] = os.path.join(config["var_dir"], "ssh_key")

    for s in config["sources"]:
        # Check for missing usernames/passwords
        if not (
            "username" in config["sources"][s] or "password" in config["sources"][s]
        ):
            if "username" not in config["sources"][s]:
                config["sources"][s]["username"] = str(uuid.uuid4())
            if "password" not in config["sources"][s]:
                config["sources"][s]["password"] = "".join(
                    random.choice(string.ascii_letters + string.digits)
                    for i in range(30)
                )
            with safe_write(os.path.join(var_sources_d, "10-" + s + ".json")) as f:
                os.fchmod(f.fileno(), 0o600)
                json_dump_p(
                    {
                        s: {
                            "username": config["sources"][s]["username"],
                            "password": config["sources"][s]["password"],
                        }
                    },
                    f,
                )

    # Clean up obsolete files, if they exist
    for f in ("rsyncd.conf", "rsyncd.secrets"):
        fn = os.path.join(config["var_dir"], f)
        if os.path.exists(fn):
            os.remove(fn)


def api_call(api_url, cmd, post_data, timeout=5):
    """Turku API call client"""
    url = urllib.parse.urljoin(api_url + "/", cmd)
    headers = {"Accept": "application/json"}
    logging.debug("API request: {} {}".format(url, post_data))
    r = requests.post(url, json=post_data, headers=headers, timeout=timeout)
    r.raise_for_status()
    response_json = r.json()
    logging.debug("API response: {} {}".format(r.status_code, response_json))
    return response_json
