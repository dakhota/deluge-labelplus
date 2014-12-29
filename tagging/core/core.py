#
# core.py
#
# Copyright (C) 2014 Ratanak Lun <ratanakvlun@gmail.com>
#
# This module is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
# Linking this software with other modules is making a combined work
# based on this software. Thus, the terms and conditions of the GNU
# General Public License cover the whole combination.
#
# As a special exception, the copyright holders of this software give
# you permission to link this software with independent modules to
# produce a combined work, regardless of the license terms of these
# independent modules, and to copy and distribute the resulting work
# under terms of your choice, provided that you also meet, for each
# linked module in the combined work, the terms and conditions of the
# license of that module. An independent module is a module which is
# not derived from or based on this software. If you modify this
# software, you may extend this exception to your version of the
# software, but you are not obligated to do so. If you do not wish to
# do so, delete this exception statement from your version.
#


import cPickle
import copy
import datetime
import logging
import os

import twisted.internet

import deluge.common
import deluge.component
import deluge.configmanager
import deluge.core.rpcserver

import tagging.common
import tagging.common.config
import tagging.common.config.convert
import tagging.common.config.autotag
import tagging.common.tag

import tagging.core.config
import tagging.core.config.convert


from deluge.plugins.pluginbase import CorePluginBase

from tagging.common import TagUpdate
from tagging.common import TaggingError


from tagging.common.literals import (
  ERR_CORE_NOT_INITIALIZED,
  ERR_INVALID_TAG, ERR_INVALID_PARENT, ERR_TAG_EXISTS,
)

CORE_CONFIG = "%s.conf" % tagging.common.MODULE_NAME
DELUGE_CORE_CONFIG = "core.conf"

CONFIG_SAVE_INTERVAL = 60*2


log = logging.getLogger(__name__)


def cmp_length_then_value(x, y):

  if len(x) > len(y): return -1
  if len(x) < len(y): return 1

  return cmp(x, y)


def check_init(func):

  def wrap(*args, **kwargs):

    if args and isinstance(args[0], Core):
      if not args[0]._initialized:
        raise TaggingError(ERR_CORE_NOT_INITIALIZED)

    return func(*args, **kwargs)


  return wrap


class Core(CorePluginBase):

  # Section: Initialization

  def __init__(self, plugin_name):

    super(Core, self).__init__(plugin_name)

    self._initialized = False
    self._config = None


  def enable(self):

    log.debug("Initializing %s...", self.__class__.__name__)

    if not deluge.component.get("TorrentManager").session_started:
      deluge.component.get("EventManager").register_event_handler(
        "SessionStartedEvent", self._on_session_started)
      log.debug("Waiting for session to start...")
    else:
      twisted.internet.reactor.callLater(0.1, self._initialize)


  def _on_session_started(self):

    log.debug("Resuming initialization...")

    twisted.internet.reactor.callLater(0.1, self._initialize)


  def _initialize(self):

    self._core = deluge.configmanager.ConfigManager(DELUGE_CORE_CONFIG)
    self._config = self._load_config()

    self._prefs = self._config["prefs"]
    self._tags = self._config["tags"]
    self._mappings = self._config["mappings"]

    self._sorted_tags = {}

    self._timestamp = {
      "tags_changed": tagging.common.DATETIME_010101,
      "mappings_changed": tagging.common.DATETIME_010101,
      "tags_sorted": tagging.common.DATETIME_010101,
      "last_saved": tagging.common.DATETIME_010101,
    }

    self._torrents = deluge.component.get("TorrentManager").torrents

    self._build_tag_index()
    self._remove_orphans()

    self._normalize_data()
    self._normalize_mappings()
    self._normalize_move_modes()

    self._build_fullname_index()
    self._build_shared_limit_index()

    deluge.component.get("FilterManager").register_filter(
      tagging.common.STATUS_ID, self.filter_by_tag)

    deluge.component.get("CorePluginManager").register_status_field(
      tagging.common.STATUS_NAME, self.get_torrent_tag_name)
    deluge.component.get("CorePluginManager").register_status_field(
      tagging.common.STATUS_ID, self.get_torrent_tag_id)

    deluge.component.get("EventManager").register_event_handler(
      "TorrentAddedEvent", self.on_torrent_added)
    deluge.component.get("EventManager").register_event_handler(
      "PreTorrentRemovedEvent", self.on_torrent_removed)

    deluge.component.get("AlertManager").register_handler(
      "torrent_finished_alert", self.on_torrent_finished)

    self._initialized = True

    twisted.internet.reactor.callLater(1, self._save_config_update_loop)
    twisted.internet.reactor.callLater(1, self._shared_limit_update_loop)

    log.debug("%s initialized", self.__class__.__name__)


  def _load_config(self):

    config = deluge.configmanager.ConfigManager(CORE_CONFIG)

    tagging.common.config.init_config(config,
      tagging.common.config.CONFIG_DEFAULTS,
      tagging.common.config.CONFIG_VERSION,
      tagging.core.config.convert.CONFIG_SPECS)

    tagging.core.config.remove_invalid_keys(config.config)

    return config


  def _build_tag_index(self):

    def build_tag_entry(tag_id):

      children = []
      torrents = []

      for id in self._tags:
        if id == tag_id:
          continue

        if tagging.common.tag.get_parent_id(id) == tag_id:
          children.append(id)

      for id in self._mappings:
        if self._mappings[id] == tag_id:
          torrents.append(id)

      tag_entry = {
        "children": children,
        "torrents": torrents,
      }

      return tag_entry


    index = {}

    index[tagging.common.tag.ID_NULL] = build_tag_entry(
      tagging.common.tag.ID_NULL)

    for id in self._tags:
      if id not in tagging.common.tag.RESERVED_IDS:
        index[id] = build_tag_entry(id)

    self._index = index


  def _remove_orphans(self):

    removals = []

    for id in self._tags:
      parent_id = tagging.common.tag.get_parent_id(id)
      if (parent_id != tagging.common.tag.ID_NULL and
          parent_id not in self._tags):
        removals.append(id)

    for id in removals:
      self._remove_tag(id)


  def _normalize_data(self):

    self._normalize_options(self._prefs["options"])
    self._normalize_tag_options(self._prefs["tag"])

    for id in tagging.common.tag.RESERVED_IDS:
      if id in self._tags:
        del self._tags[id]

    for id in self._tags:
      self._normalize_tag_options(self._tags[id]["options"],
        self._prefs["tag"])


  def _normalize_mappings(self):

    for id in self._mappings.keys():
      if id in self._torrents:
        if self._mappings[id] in self._tags:
          self._apply_torrent_options(id)
          continue
        else:
          self._reset_torrent_options(id)

      self._remove_torrent_tag(id)


  def _normalize_move_modes(self):

    root_ids = self._get_descendent_tags(tagging.common.tag.ID_NULL, 1)
    for id in root_ids:
      self._tags[id]["options"]["move_completed_mode"] = \
        tagging.common.config.MOVE_FOLDER


  def _build_shared_limit_index(self):

    shared_limit_index = []

    for id in self._tags:
      if (self._tags[id]["options"]["bandwidth_settings"] and
          self._tags[id]["options"]["shared_limit"]):
        shared_limit_index.append(id)

    self._shared_limit_index = shared_limit_index


  # Section: Deinitialization

  def disable(self):

    log.debug("Deinitializing %s...", self.__class__.__name__)

    deluge.component.get("EventManager").deregister_event_handler(
      "SessionStartedEvent", self._on_session_started)

    if self._config:
      if self._initialized:
        self._config.save()

      deluge.configmanager.close(CORE_CONFIG)

    self._initialized = False

    deluge.component.get("EventManager").deregister_event_handler(
      "TorrentAddedEvent", self.on_torrent_added)
    deluge.component.get("EventManager").deregister_event_handler(
      "PreTorrentRemovedEvent", self.on_torrent_removed)

    deluge.component.get("AlertManager").deregister_handler(
      self.on_torrent_finished)

    deluge.component.get("CorePluginManager").deregister_status_field(
      tagging.common.STATUS_ID)
    deluge.component.get("CorePluginManager").deregister_status_field(
      tagging.common.STATUS_NAME)

    if (tagging.common.STATUS_ID in
        deluge.component.get("FilterManager").registered_filters):
      deluge.component.get("FilterManager").deregister_filter(
        tagging.common.STATUS_ID)

    self._rpc_deregister(tagging.common.PLUGIN_NAME)

    log.debug("%s deinitialized", self.__class__.__name__)


  def _rpc_deregister(self, name):

    server = deluge.component.get("RPCServer")
    name = name.lower()

    for d in dir(self):
      if d[0] == "_": continue

      if getattr(getattr(self, d), '_rpcserver_export', False):
        method = "%s.%s" % (name, d)
        log.debug("Deregistering method: %s", method)
        if method in server.factory.methods:
          del server.factory.methods[method]


  # Section: Update Loops

  def _save_config_update_loop(self):

    if self._initialized:
      last_changed = max(self._timestamp["tags_changed"],
        self._timestamp["mappings_changed"])

      if self._timestamp["last_saved"] <= last_changed:
        self._config.save()
        self._timestamp["last_saved"] = datetime.datetime.now()

      twisted.internet.reactor.callLater(CONFIG_SAVE_INTERVAL,
        self._save_config_update_loop)


  def _shared_limit_update_loop(self):

    if self._initialized:
      for id in self._shared_limit_index:
        if id in self._tags:
          self._do_update_shared_limit(id)

      twisted.internet.reactor.callLater(
        self._prefs["options"]["shared_limit_interval"],
        self._shared_limit_update_loop)


  # Section: Public API: General

  @deluge.core.rpcserver.export
  def is_initialized(self):

    return self._initialized


  @deluge.core.rpcserver.export
  def get_daemon_info(self):

    return self._get_daemon_info()


  # Section: Public API: Preferences

  @deluge.core.rpcserver.export
  @check_init
  def get_preferences(self):

    log.debug("Getting preferences")

    return self._prefs


  @deluge.core.rpcserver.export
  @check_init
  def set_preferences(self, prefs):

    log.debug("Setting preferences")

    self._normalize_options(prefs["options"])
    self._prefs["options"].update(prefs["options"])

    self._normalize_tag_options(prefs["tag"])
    self._prefs["tag"].update(prefs["tag"])

    self._config.save()
    self._timestamp["last_saved"] = datetime.datetime.now()


  @deluge.core.rpcserver.export
  @check_init
  def get_tag_defaults(self):

    log.debug("Getting tag defaults")

    return self._prefs["tag"]


  # Section: Public API: Tag: Queries

  @deluge.core.rpcserver.export
  @check_init
  def get_move_path_options(self, tag_id):

    if tag_id not in self._tags:
      raise TaggingError(ERR_INVALID_TAG)

    parent_path = self._get_parent_move_path(tag_id)

    options = {
      "parent": parent_path,
      "subfolder": os.path.join(parent_path, self._tags[tag_id]["name"]),
    }

    return options


  @deluge.core.rpcserver.export
  @check_init
  def get_tag_bandwidth_usages(self, tag_ids):

    usages = {}

    for id in set(tag_ids):
      if id == tagging.common.tag.ID_NONE or id in self._tags:
        usages[id] = self._get_tag_bandwidth_usage(id)

    return usages


  # Deprecated
  @deluge.core.rpcserver.export
  @check_init
  def get_tags_data(self, timestamp=None):

    if timestamp:
      t = cPickle.loads(timestamp)
    else:
      t = tagging.common.DATETIME_010101

    last_changed = max(self._timestamp["tags_changed"],
      self._timestamp["mappings_changed"])

    if t <= last_changed:
      return self._get_tags_data()
    else:
      return None


  @deluge.core.rpcserver.export
  @check_init
  def get_tag_updates(self, since=None):

    if since:
      t = cPickle.loads(since)
    else:
      t = tagging.common.DATETIME_010101

    last_changed = max(self._timestamp["tags_changed"],
      self._timestamp["mappings_changed"])

    if t <= last_changed:
      u = TagUpdate(TagUpdate.TYPE_FULL, datetime.datetime.now(),
        self._get_tags_data())
      return cPickle.dumps(u)
    else:
      return None


  # New get_tag_updates candidate, use dict instead of TagUpdate class
  @deluge.core.rpcserver.export
  @check_init
  def get_tag_updates_dict(self, since=None):

    if since:
      t = cPickle.loads(since)
    else:
      t = tagging.common.DATETIME_010101

    last_changed = max(self._timestamp["tags_changed"],
      self._timestamp["mappings_changed"])

    if t <= last_changed:
      u = TagUpdate(TagUpdate.TYPE_FULL, datetime.datetime.now(),
        self._get_tags_data())

      return {
        "type": u.type,
        "timestamp": cPickle.dumps(u.timestamp),
        "data": u.data
      }
    else:
      return None


  # Section: Public API: Tag: Modifiers

  @deluge.core.rpcserver.export
  @check_init
  def add_tag(self, parent_id, tag_name):

    log.debug("Adding %r to tag %r", tag_name, parent_id)

    if (parent_id != tagging.common.tag.ID_NULL and
        parent_id not in self._tags):
      raise TaggingError(ERR_INVALID_PARENT)

    id = self._add_tag(parent_id, tag_name)

    self._timestamp["tags_changed"] = datetime.datetime.now()

    return id


  @deluge.core.rpcserver.export
  @check_init
  def rename_tag(self, tag_id, tag_name):

    log.debug("Renaming name of tag %r to %r", tag_id, tag_name)

    if tag_id not in self._tags:
      raise TaggingError(ERR_INVALID_TAG)

    self._rename_tag(tag_id, tag_name)

    self._timestamp["tags_changed"] = datetime.datetime.now()


  @deluge.core.rpcserver.export
  @check_init
  def move_tag(self, tag_id, dest_id, dest_name):

    log.debug("Moving tag %r to %r with name %r", tag_id, dest_id,
      dest_name)

    if tag_id not in self._tags:
      raise TaggingError(ERR_INVALID_TAG)

    if dest_id != tagging.common.tag.ID_NULL and (
        tag_id == dest_id or dest_id not in self._tags or
        tagging.common.tag.is_ancestor(tag_id, dest_id)):
      raise TaggingError(ERR_INVALID_PARENT)

    parent_id = tagging.common.tag.get_parent_id(tag_id)
    if parent_id == dest_id:
      self._rename_tag(tag_id, dest_name)
    else:
      self._move_tag(tag_id, dest_id, dest_name)

    self._timestamp["tags_changed"] = datetime.datetime.now()


  @deluge.core.rpcserver.export
  @check_init
  def remove_tag(self, tag_id):

    log.debug("Removing tag %r", tag_id)

    if tag_id not in self._tags:
      raise TaggingError(ERR_INVALID_TAG)

    self._remove_tag(tag_id)

    self._timestamp["tags_changed"] = datetime.datetime.now()
    self._timestamp["mappings_changed"] = datetime.datetime.now()


  # Section: Public API: Tag: Options

  @deluge.core.rpcserver.export
  @check_init
  def get_tag_options(self, tag_id):

    log.debug("Getting tag options for %r", tag_id)

    if tag_id not in self._tags:
      raise TaggingError(ERR_INVALID_TAG)

    return self._tags[tag_id]["options"]


  @deluge.core.rpcserver.export
  @check_init
  def set_tag_options(self, tag_id, options_in, apply_to_all=None):

    log.debug("Setting tag options for %r", tag_id)

    if tag_id not in self._tags:
      raise TaggingError(ERR_INVALID_TAG)

    self._set_tag_options(tag_id, options_in, apply_to_all)


  # Section: Public API: Torrent-Tag

  @deluge.core.rpcserver.export
  @check_init
  def get_torrent_tags(self, torrent_ids):

    log.debug("Getting torrent tags")

    mappings = {}

    for id in set(torrent_ids):
      if id in self._torrents:
        mappings[id] = [
          self._get_torrent_tag_id(id),
          self._get_torrent_tag_name(id),
        ]

    return mappings


  @deluge.core.rpcserver.export
  @check_init
  def set_torrent_tags(self, torrent_ids, tag_id):

    log.debug("Setting torrent tags to %r", tag_id)

    if (tag_id != tagging.common.tag.ID_NONE and
        tag_id not in self._tags):
      raise TaggingError(ERR_INVALID_TAG)

    torrent_ids = [x for x in set(torrent_ids) if x in self._torrents]

    for id in torrent_ids:
      self._set_torrent_tag(id, tag_id)

    do_move = False

    if self._prefs["options"]["move_on_changes"]:
      if tag_id == tagging.common.tag.ID_NONE:
        do_move = self._core["move_completed"]
      else:
        options = self._tags[tag_id]["options"]
        do_move = options["download_settings"] and options["move_completed"]

    if do_move:
      self._do_move_completed(torrent_ids)

    if torrent_ids:
      self._timestamp["mappings_changed"] = datetime.datetime.now()


  # Section: Public Callbacks

  @check_init
  def on_torrent_added(self, torrent_id):

    tag_id = self._find_autotag_match(torrent_id)
    if tag_id:
      self._set_torrent_tag(torrent_id, tag_id)
      log.debug("Setting torrent %r to tag %r", torrent_id, tag_id)

      self._timestamp["mappings_changed"] = datetime.datetime.now()


  @check_init
  def on_torrent_removed(self, torrent_id):

    if torrent_id in self._mappings:
      tag_id = self._mappings[torrent_id]
      self._remove_torrent_tag(torrent_id)
      log.debug("Removing torrent %r from tag %r", torrent_id, tag_id)

    self._timestamp["mappings_changed"] = datetime.datetime.now()


  @check_init
  def on_torrent_finished(self, alert):

    torrent_id = str(alert.handle.info_hash())

    if torrent_id in self._mappings:
      log.debug("Lagged torrent %r has finished", torrent_id)

      if self._prefs["options"]["move_after_recheck"]:
        # Try to move in case this alert was from a recheck

        tag_id = self._mappings[torrent_id]
        options = self._tags[tag_id]["options"]

        if options["download_settings"] and options["move_completed"]:
          self._do_move_completed([torrent_id])


  @check_init
  def get_torrent_tag_id(self, torrent_id):

    return self._get_torrent_tag_id(torrent_id)


  @check_init
  def get_torrent_tag_name(self, torrent_id):

    return self._get_torrent_tag_name(torrent_id)


  @check_init
  def filter_by_tag(self, torrent_ids, tag_ids):

    return self._filter_by_tag(torrent_ids, tag_ids)


  # Section: General

  def _get_daemon_info(self):

    info = {
      "os.path": os.path.__name__,
    }

    return info


  def _get_deluge_save_path(self):

    path = self._core["download_location"]
    if not path:
      path = deluge.common.get_default_download_dir()

    return path


  def _get_deluge_move_path(self):

    path = self._core["move_completed_path"]
    if not path:
      path = self._get_deluge_save_path()

    return path


  # Section: Options

  def _normalize_options(self, options):

    for key in options.keys():
      if key not in tagging.common.config.OPTION_DEFAULTS:
        del options[key]

    for key in tagging.common.config.OPTION_DEFAULTS:
      if key not in options:
        options[key] = copy.deepcopy(
          tagging.common.config.OPTION_DEFAULTS[key])

    if options["shared_limit_interval"] < 1:
      options["shared_limit_interval"] = 1


  # Section: Tag: Queries

  def _get_unused_id(self, parent_id):

    assert(parent_id == tagging.common.tag.ID_NULL or
      parent_id in self._tags)

    i = 0
    tag_obj = {}

    if parent_id == tagging.common.tag.ID_NULL:
      prefix = ""
    else:
      prefix = "%s:" % parent_id

    while tag_obj is not None:
      id = "%s%s" % (prefix, i)
      tag_obj = self._tags.get(id)
      i += 1

    return id


  def _get_children_names(self, parent_id):

    assert(parent_id == tagging.common.tag.ID_NULL or
      parent_id in self._tags)

    names = []

    for id in self._index[parent_id]["children"]:
      names.append(self._tags[id]["name"])

    return names


  def _validate_name(self, parent_id, tag_name):

    assert(parent_id == tagging.common.tag.ID_NULL or
      parent_id in self._tags)

    tagging.common.tag.validate_name(tag_name)

    names = self._get_children_names(parent_id)
    if tag_name in names:
      raise TaggingError(ERR_TAG_EXISTS)


  def _get_descendent_tags(self, tag_id, depth=-1):

    assert(tag_id == tagging.common.tag.ID_NULL or
      tag_id in self._tags)

    descendents = []

    if depth == -1 or depth > 0:
      if depth > 0:
        depth -= 1

      for id in self._index[tag_id]["children"]:
        descendents.append(id)
        descendents += self._get_descendent_tags(id, depth)

    return descendents


  def _get_parent_move_path(self, tag_id):

    assert(tag_id in self._tags)

    parent_id = tagging.common.tag.get_parent_id(tag_id)
    if parent_id in self._tags:
      path = self._tags[parent_id]["options"]["move_completed_path"]
    else:
      path = self._get_deluge_move_path()

    return path


  def _resolve_move_path(self, tag_id):

    assert(tag_id in self._tags)

    name = self._tags[tag_id]["name"]
    options = self._tags[tag_id]["options"]

    mode = options["move_completed_mode"]
    path = options["move_completed_path"]

    if mode != tagging.common.config.MOVE_FOLDER:
      path = self._get_parent_move_path(tag_id)

      if mode == tagging.common.config.MOVE_SUBFOLDER:
        path = os.path.join(path, name)

    return path


  def _get_tag_bandwidth_usage(self, tag_id):

    assert(tag_id == tagging.common.tag.ID_NONE or
      tag_id in self._tags)

    if tag_id == tagging.common.tag.ID_NONE:
      torrent_ids = self._get_untagged_torrents()
    else:
      torrent_ids = self._index[tag_id]["torrents"]

    return self._get_torrent_bandwidth_usage(torrent_ids)


  def _get_sorted_tags(self, cmp_func=None, reverse=False):

    if self._timestamp["tags_sorted"] <= self._timestamp["tags_changed"]:
      self._sorted_tags.clear()

    key = (cmp_func, reverse)

    if key not in self._sorted_tags:
      self._sorted_tags[key] = sorted(self._tags,
        cmp=key[0], reverse=key[1])

      self._timestamp["tags_sorted"] = datetime.datetime.now()

    return self._sorted_tags[key]


  def _get_tags_data(self):

    total_count = len(self._torrents)
    tagged_count = 0
    data = {}

    tag_ids = self._get_sorted_tags(cmp_length_then_value)

    for id in tag_ids:
      count = len(self._index[id]["torrents"])
      tagged_count += count

      data[id] = {
        "name": self._tags[id]["name"],
        "count": count,
      }

    data[tagging.common.tag.ID_ALL] = {
      "name": tagging.common.tag.ID_ALL,
      "count": total_count,
    }

    data[tagging.common.tag.ID_NONE] = {
      "name": tagging.common.tag.ID_NONE,
      "count": total_count-lagged_count,
    }

    return data


  # Section: Tag: Modifiers

  def _add_tag(self, parent_id, tag_name):

    assert(parent_id == tagging.common.tag.ID_NULL or
      parent_id in self._tags)

    tag_name = tag_name.strip()

    try:
      tag_name = unicode(tag_name, "utf8")
    except (TypeError, UnicodeDecodeError):
      pass

    self._validate_name(parent_id, tag_name)

    id = self._get_unused_id(parent_id)
    self._index[parent_id]["children"].append(id)

    self._tags[id] = {
      "name": tag_name,
      "options": copy.deepcopy(self._prefs["tag"]),
    }

    self._index[id] = {
      "fullname": self._resolve_fullname(id),
      "children": [],
      "torrents": [],
    }

    self._tags[id]["options"]["move_completed_path"] = \
      self._resolve_move_path(id)

    if self._tags[id]["options"]["shared_limit"]:
      self._shared_limit_index.append(id)

    return id


  def _rename_tag(self, tag_id, tag_name):

    assert(tag_id in self._tags)

    tag_name = tag_name.strip()

    try:
      tag_name = unicode(tag_name, "utf8")
    except (TypeError, UnicodeDecodeError):
      pass

    parent_id = tagging.common.tag.get_parent_id(tag_id)
    self._validate_name(parent_id, tag_name)
    self._tags[tag_id]["name"] = tag_name

    self._build_fullname_index(tag_id)
    self._update_move_completed_paths(tag_id)

    if self._prefs["options"]["move_on_changes"]:
      self._do_move_completed_by_tag(tag_id, True)


  def _move_tag(self, tag_id, dest_id, dest_name):

    def reparent(tag_id, dest_id):

      id = self._get_unused_id(dest_id)
      self._index[dest_id]["children"].append(id)

      self._tags[id] = self._tags[tag_id]

      self._index[id] = {
        "fullname": self._resolve_fullname(id),
        "torrents": self._index[tag_id]["torrents"],
        "children": [],
      }

      if tag_id in self._shared_limit_index:
        self._shared_limit_index.remove(tag_id)
        self._shared_limit_index.append(id)

      parent_id = tagging.common.tag.get_parent_id(tag_id)
      if parent_id in self._index:
        self._index[parent_id]["children"].remove(tag_id)

      for torrent_id in self._index[tag_id]["torrents"]:
        self._mappings[torrent_id] = id

      for child_id in list(self._index[tag_id]["children"]):
        reparent(child_id, id)

      del self._index[tag_id]
      del self._tags[tag_id]

      return id


    assert(tag_id in self._tags)
    assert(dest_id == tagging.common.tag.ID_NULL or
      dest_id in self._tags)

    dest_name = dest_name.strip()

    try:
      dest_name = unicode(dest_name, "utf8")
    except (TypeError, UnicodeDecodeError):
      pass

    self._validate_name(dest_id, dest_name)
    id = reparent(tag_id, dest_id)

    self._tags[id]["name"] = dest_name
    self._update_move_completed_paths(id)

    if self._prefs["options"]["move_on_changes"]:
      self._do_move_completed_by_tag(id, subtags=True)


  def _remove_tag(self, tag_id):

    assert(tag_id in self._tags)

    if tag_id in self._shared_limit_index:
      self._shared_limit_index.remove(tag_id)

    parent_id = tagging.common.tag.get_parent_id(tag_id)
    if parent_id in self._index:
      self._index[parent_id]["children"].remove(tag_id)

    for id in list(self._index[tag_id]["children"]):
      self._remove_tag(id)

    torrent_ids = []

    for id in list(self._index[tag_id]["torrents"]):
      if id in self._torrents:
        self._set_torrent_tag(id, tagging.common.tag.ID_NONE)
        torrent_ids.append(id)

    del self._index[tag_id]
    del self._tags[tag_id]

    if (self._prefs["options"]["move_on_changes"] and
        self._core["move_completed"]):
      self._do_move_completed(torrent_ids)


  # Section: Tag: Options

  def _normalize_tag_options(self, options,
      template=tagging.common.config.TAG_DEFAULTS):

    for key in options.keys():
      if key not in template:
        del options[key]

    for key in template:
      if key not in options:
        options[key] = copy.deepcopy(template[key])

    options["move_completed_path"] = \
      options["move_completed_path"].strip()

    if not options["move_completed_path"]:
      options["move_completed_mode"] = \
        tagging.common.config.MOVE_FOLDER
      options["move_completed_path"] = self._get_deluge_move_path()

    if (options["move_completed_mode"] not in
        tagging.common.config.MOVE_MODES):
      options["move_completed_mode"] = \
        tagging.common.config.MOVE_FOLDER

    options["max_connections"] = int(options["max_connections"])
    options["max_upload_slots"] = int(options["max_upload_slots"])

    rules = options["autotag_rules"]
    options["autotag_rules"] = list(options["autotag_rules"])
    for rule in rules:
      if len(rule) != tagging.common.config.autotag.NUM_FIELDS:
        options["autotag_rules"].remove(rule)
        continue

      prop, op, case, query = rule
      if (prop not in tagging.common.config.autotag.PROPS or
          op not in tagging.common.config.autotag.OPS or
          case not in tagging.common.config.autotag.CASES or
          not query):
        options["autotag_rules"].remove(rule)


  def _set_tag_options(self, tag_id, options_in, apply_to_all=None):

    assert(tag_id in self._tags)

    options = self._tags[tag_id]["options"]

    old = {
      "download_settings": options["download_settings"],
      "move_completed": options["move_completed"],
      "move_completed_path": options["move_completed_path"],
    }

    self._normalize_tag_options(options_in, self._prefs["tag"])
    options.update(options_in)

    if tag_id in self._shared_limit_index:
      self._shared_limit_index.remove(tag_id)

    if options["bandwidth_settings"] and options["shared_limit"]:
      self._shared_limit_index.append(tag_id)

    for id in self._index[tag_id]["torrents"]:
      self._apply_torrent_options(id)

    # If move completed was just turned on and move on changes enabled...
    if (options["download_settings"] and options["move_completed"] and
        (not old["download_settings"] or not old["move_completed"]) and
        self._prefs["options"]["move_on_changes"]):
      self._do_move_completed_by_tag(tag_id)

    if options["move_completed_path"] != old["move_completed_path"]:
    # Path was modified; make sure descendent paths are updated
      for id in self._index[tag_id]["children"]:
        self._update_move_completed_paths(id)

        if self._prefs["options"]["move_on_changes"]:
          self._do_move_completed_by_tag(id, True)

      if self._prefs["options"]["move_on_changes"]:
        self._do_move_completed_by_tag(tag_id)

      self._timestamp["tags_changed"] = datetime.datetime.now()

    if options["autotag_settings"] and apply_to_all is not None:
      self._do_autotag_torrents(tag_id, apply_to_all)


  # Section: Tag: Full Name

  def _resolve_fullname(self, tag_id):

    assert(tag_id == tagging.common.tag.ID_NULL or
      tag_id in self._tags)

    parts = []
    id = tag_id

    while id != tagging.common.tag.ID_NULL:
      parts.append(self._tags[id]["name"])
      id = tagging.common.tag.get_parent_id(id)

    fullname = "/".join(reversed(parts))

    return fullname


  def _build_fullname_index(self, tag_id=tagging.common.tag.ID_NULL):

    assert(tag_id == tagging.common.tag.ID_NULL or
      tag_id in self._tags)

    self._index[tag_id]["fullname"] = self._resolve_fullname(tag_id)

    for id in self._index[tag_id]["children"]:
      self._build_fullname_index(id)


  # Section: Tag: Shared Limit

  def _do_update_shared_limit(self, tag_id):

    assert(tag_id in self._tags)

    options = self._tags[tag_id]["options"]
    shared_download_limit = options["max_download_speed"]
    shared_upload_limit = options["max_upload_speed"]

    if shared_download_limit < 0.0 and shared_upload_limit < 0.0:
      return

    torrent_ids = self._index[tag_id]["torrents"]

    statuses = self._get_torrent_statuses(
      torrent_ids, {"state": ["Seeding", "Downloading"]},
      ["download_payload_rate", "upload_payload_rate"])

    num_active_downloads = \
      sum(1 for id in statuses if statuses[id]["download_payload_rate"] > 0.0)
    download_rate_sum = \
      sum(statuses[id]["download_payload_rate"] for id in statuses) / 1024.0
    download_diff = download_rate_sum - shared_download_limit

    num_active_uploads = \
      sum(1 for id in statuses if statuses[id]["upload_payload_rate"] > 0.0)
    upload_rate_sum = \
      sum(statuses[id]["upload_payload_rate"] for id in statuses) / 1024.0
    upload_diff = upload_rate_sum - shared_upload_limit

    # Modify individual torrent bandwidth limits based on shared limit
    for id in statuses:
      torrent = self._torrents[id]
      status = statuses[id]

      # Determine new torrent download limit
      if shared_download_limit < 0.0:
        torrent.set_max_download_speed(-1.0)
      else:
        download_rate = status["download_payload_rate"] / 1024.0
        limit = download_rate

        if download_diff >= 0.0:
        # Total is above shared limit; deduct based on usage
          usage_ratio = download_rate / download_rate_sum
          limit -= (usage_ratio * download_diff)
        elif download_rate > 0.0:
        # Total is below and torrent active; increase by a slice of unused
          limit += abs(download_diff) / num_active_downloads
        else:
        # Total is below and torrent inactive; give chance by setting max
          limit = shared_download_limit

        if limit < 0.1: limit = 0.1
        torrent.set_max_download_speed(limit)

      # Determine new torrent upload limit
      if shared_upload_limit < 0.0:
        torrent.set_max_upload_speed(-1.0)
      else:
        upload_rate = status["upload_payload_rate"] / 1024.0
        limit = upload_rate

        if upload_diff >= 0.0:
          usage_ratio = upload_rate / upload_rate_sum
          limit -= (usage_ratio * upload_diff)
        elif upload_rate > 0.0:
          limit += abs(upload_diff) / num_active_uploads
        else:
          limit = shared_upload_limit

        if limit < 0.1: limit = 0.1
        torrent.set_max_upload_speed(limit)


  # Section: Torrent: Queries

  def _get_torrent_statuses(self, torrent_ids, filters, fields):

    assert(all(x in self._torrents for x in torrent_ids))

    statuses = {}

    for filter in filters:
      if filter not in fields:
        fields.append(filter)

    for id in torrent_ids:
      status = self._torrents[id].get_status(fields)

      if not filters:
        statuses[id] = status
      else:
        passed = True

        for filter in filters:
          if status[filter] not in filters[filter]:
            passed = False
            break

        if passed:
          statuses[id] = status

    return statuses


  def _get_torrent_bandwidth_usage(self, torrent_ids):

    assert(all(x in self._torrents for x in torrent_ids))

    statuses = self._get_torrent_statuses(
      torrent_ids, {"state": ["Seeding", "Downloading"]},
      ["download_payload_rate", "upload_payload_rate"])

    download_rate_sum = 0.0
    upload_rate_sum = 0.0

    for id in statuses:
      status = statuses[id]
      download_rate_sum += status["download_payload_rate"]
      upload_rate_sum += status["upload_payload_rate"]

    return (download_rate_sum, upload_rate_sum)


  # Section: Torrent: Modifiers

  def _reset_torrent_options(self, torrent_id):

    assert(torrent_id in self._torrents)

    torrent = self._torrents[torrent_id]

    # Download settings
    torrent.set_move_completed(self._core["move_completed"])
    torrent.set_move_completed_path(self._core["move_completed_path"])
    torrent.set_prioritize_first_last(
      self._core["prioritize_first_last_pieces"])

    # Bandwidth settings
    torrent.set_max_download_speed(
      self._core["max_download_speed_per_torrent"])
    torrent.set_max_upload_speed(self._core["max_upload_speed_per_torrent"])
    torrent.set_max_connections(self._core["max_connections_per_torrent"])
    torrent.set_max_upload_slots(self._core["max_upload_slots_per_torrent"])

    # Queue settings
    torrent.set_auto_managed(self._core["auto_managed"])
    torrent.set_stop_at_ratio(self._core["stop_seed_at_ratio"])
    torrent.set_stop_ratio(self._core["stop_seed_ratio"])
    torrent.set_remove_at_ratio(self._core["remove_seed_at_ratio"])


  def _apply_torrent_options(self, torrent_id):

    assert(torrent_id in self._torrents)

    tag_id = self._mappings.get(torrent_id, tagging.common.tag.ID_NONE)

    if tag_id == tagging.common.tag.ID_NONE:
      self._reset_torrent_options(torrent_id)
      return

    options = self._tags[tag_id]["options"]
    torrent = self._torrents[torrent_id]

    if options["download_settings"]:
      torrent.set_move_completed(options["move_completed"])
      torrent.set_prioritize_first_last(options["prioritize_first_last"])

      if options["move_completed"]:
        torrent.set_move_completed_path(options["move_completed_path"])

    if options["bandwidth_settings"]:
      torrent.set_max_download_speed(options["max_download_speed"])
      torrent.set_max_upload_speed(options["max_upload_speed"])
      torrent.set_max_connections(options["max_connections"])
      torrent.set_max_upload_slots(options["max_upload_slots"])

    if options["queue_settings"]:
      torrent.set_auto_managed(options["auto_managed"])
      torrent.set_stop_at_ratio(options["stop_at_ratio"])

      if options["stop_at_ratio"]:
        torrent.set_stop_ratio(options["stop_ratio"])
        torrent.set_remove_at_ratio(options["remove_at_ratio"])


  # Section: Torrent-Tag: Queries

  def _get_untagged_torrents(self):

    torrent_ids = []

    for id in self._torrents:
      if id not in self._mappings:
        torrent_ids.append(id)

    return torrent_ids


  def _get_torrent_tag_id(self, torrent_id):

    return self._mappings.get(torrent_id, tagging.common.tag.ID_NONE)


  def _get_torrent_tag_name(self, torrent_id):

    tag_id = self._mappings.get(torrent_id, tagging.common.tag.ID_NONE)
    if tag_id == tagging.common.tag.ID_NONE:
      return ""

    return self._index[tag_id]["fullname"]


  def _filter_by_tag(self, torrent_ids, tag_ids):

    filtered = []

    for id in torrent_ids:
      tag_id = self._mappings.get(id, tagging.common.tag.ID_NONE)
      if tag_id in tag_ids:
        filtered.append(id)

    return filtered


  # Section: Torrent-Tag: Modifiers

  def _remove_torrent_tag(self, torrent_id):

    tag_id = self._mappings.get(torrent_id, tagging.common.tag.ID_NONE)
    if tag_id in self._index:
      self._index[tag_id]["torrents"].remove(torrent_id)

    del self._mappings[torrent_id]


  def _set_torrent_tag(self, torrent_id, tag_id):

    assert(torrent_id in self._torrents)
    assert(tag_id == tagging.common.tag.ID_NONE or
      tag_id in self._tags)

    if torrent_id in self._mappings:
      self._remove_torrent_tag(torrent_id)

    if tag_id == tagging.common.tag.ID_NONE:
      self._reset_torrent_options(torrent_id)
    else:
      self._mappings[torrent_id] = tag_id
      self._index[tag_id]["torrents"].append(torrent_id)
      self._apply_torrent_options(torrent_id)


  # Section: Torrent-Tag: Autotag

  def _has_autotag_match(self, torrent_id, tag_id):

    assert(torrent_id in self._torrents)
    assert(tag_id in self._tags)

    options = self._tags[tag_id]["options"]
    rules = options["autotag_rules"]
    match_all = options["autotag_match_all"]

    status = self._torrents[torrent_id].get_status(["name", "trackers"])
    name = status["name"]
    trackers = [x["url"] for x in status["trackers"]]

    props = {
      tagging.common.config.autotag.PROP_NAME: [name],
      tagging.common.config.autotag.PROP_TRACKER: trackers,
    }

    return tagging.common.config.autotag.find_match(props,
      rules, match_all)


  def _find_autotag_match(self, torrent_id):

    assert(torrent_id in self._torrents)

    tag_ids = self._get_sorted_tags(cmp_length_then_value)

    for id in tag_ids:
      if self._tags[id]["options"]["autotag_settings"]:
        if self._has_autotag_match(torrent_id, id):
          return id

    return tagging.common.tag.ID_NONE


  def _do_autotag_torrents(self, tag_id, apply_to_all=False):

    assert(tag_id in self._tags)

    changed = False

    for id in self._torrents:
      if apply_to_all or id not in self._mappings:
        if self._has_autotag_match(id, tag_id):
          self._set_torrent_tag(id, tag_id)
          changed = True

    if changed:
      self._timestamp["mappings_changed"] = datetime.datetime.now()


  # Section: Torrent-Tag: Move Completed

  def _apply_move_completed_path(self, tag_id):

    assert(tag_id in self._tags)

    for id in self._index[tag_id]["torrents"]:
      self._torrents[id].set_move_completed_path(
          self._tags[tag_id]["options"]["move_completed_path"])


  def _update_move_completed_paths(self, tag_id):

    assert(tag_id in self._tags)

    options = self._tags[tag_id]["options"]

    path = self._resolve_move_path(tag_id)
    if path == options["move_completed_path"]:
      return

    options["move_completed_path"] = path

    if options["download_settings"] and options["move_completed"]:
      self._apply_move_completed_path(tag_id)

    for id in self._index[tag_id]["children"]:
      self._update_move_completed_paths(id)


  def _do_move_completed(self, torrent_ids):

    assert(all(x in self._torrents for x in torrent_ids))

    for id in torrent_ids:
      torrent = self._torrents[id]
      status = torrent.get_status(["save_path", "move_completed_path"])

      tag_id = self._mappings.get(id, tagging.common.tag.ID_NONE)
      if tag_id == tagging.common.tag.ID_NONE:
        dest_path = status["move_completed_path"]
      else:
        options = self._tags[tag_id]["options"]
        dest_path = options["move_completed_path"]

      if torrent.handle.is_finished() and dest_path != status["save_path"]:
        torrent.move_storage(dest_path)


  def _do_move_completed_by_tag(self, tag_id, subtags=False):

    assert(tag_id in self._tags)

    options = self._tags[tag_id]["options"]

    if options["download_settings"] and options["move_completed"]:
      self._do_move_completed(self._index[tag_id]["torrents"])

    if subtags:
      for id in self._index[tag_id]["children"]:
        self._do_move_completed_by_tag(id, subtags)
